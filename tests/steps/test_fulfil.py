"""A person's Fulfil: the write token reaches that walk alone, never a round's."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from convergence.clones import Clones
from convergence.config import settings
from convergence.engine import Engine
from convergence.journals import Journals
from convergence.page import create_app
from convergence.tokens import READ, Tokens

from .fakes import Runner, Storage, journal

SITE = "https://convergence.example"
WHO = "someone@example.org"


class Caller:
    """GitHub's token mint: what was asked, and a token per call."""

    def __init__(self, ok=True):
        self.asked, self.ok = [], ok

    def call(self, plugin, operation, request):
        self.asked.append(request)
        n = len(self.asked)

        class Answer:
            ok, status = self.ok, 200 if self.ok else 403

            def json(self):
                return {"token": f"ghs_{n}"}
        return Answer()


def test_the_rounds_token_may_only_read_and_is_reused():
    caller, now = Caller(), [1000.0]
    tokens = Tokens(caller, "1", "key", "9", sign=lambda *a: "jwt", clock=lambda: now[0])
    assert tokens.read() == "ghs_1" and tokens.read() == "ghs_1"
    assert json.loads(caller.asked[0].body) == {"permissions": READ}
    assert all(v == "read" for v in READ.values())
    now[0] += 46 * 60
    assert tokens.read() == "ghs_2"


def test_a_fulfils_token_has_the_installations_permissions_and_is_minted_each_time():
    caller = Caller()
    tokens = Tokens(caller, "1", "key", "9", sign=lambda *a: "jwt")
    assert tokens.write() == "ghs_1" and tokens.write() == "ghs_2"
    assert caller.asked[0].body is None


def test_a_refused_mint_raises():
    from integrations.calls import Refused
    with pytest.raises(Refused):
        Tokens(Caller(ok=False), "1", "key", "9", sign=lambda *a: "jwt").read()


class World:
    def __init__(self, tmp: Path, fulfil=True):
        (tmp / "storage-token").write_text("storage-token")
        (tmp / "write-token").write_text("bws-write")
        self.runner, self.lines = Runner(), []
        self.runner.states["Sustenaut/release-state"] = json.dumps(
            {"owner": "Sustenaut", "repos": {"notifier": {"deploy": True}}})
        self.runner.walk_prints("Sustenaut/notifier", journal(states={"k8s.secret:s": "missing"}), code=1)
        env = {"CONVERGENCE_STORAGE_URL": "https://storage.test", "CONVERGENCE_WORK": str(tmp),
               "CONVERGENCE_STORAGE_TOKEN_FILE": str(tmp / "storage-token"),
               "CONVERGENCE_BITWARDEN_WRITE_TOKEN_FILE": str(tmp / "write-token")}
        from convergence.walker import walk

        def walker(*a, **k):
            return walk(*a, **k, runner=self.runner)
        self.engine = Engine(settings(env), Clones(tmp, self.runner, {"PATH": "/bin"}), lambda: "ghs_read", None,
                             base_env={"PATH": "/bin"}, walker=walker, log=self.lines.append,
                             fulfil_token=(lambda: "ghs_write") if fulfil else None)

    def walk_envs(self, fulfil: bool):
        return [env for argv, _, env in self.runner.calls if argv[0] != "git" and ("--fulfil" in argv) == fulfil]

    def wait(self, job):
        for t in threading.enumerate():
            if t.name == f"fulfil {job.id}":
                t.join(10)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    w.engine.round()
    return w


def test_a_rounds_walk_holds_no_write_token(world):
    (env,) = world.walk_envs(fulfil=False)
    assert "PUBSEC_WRITE_TOKEN_FILE" not in env and env["GH_TOKEN"] == "ghs_read"
    assert "bws-write" not in json.dumps(env)


def test_a_fulfil_walks_one_unit_with_the_write_tokens_as_the_person(world, tmp_path):
    world.runner.walk_prints("Sustenaut/notifier", journal(states={"k8s.secret:s": "ready"}))
    job = world.engine.fulfil("Sustenaut/notifier", "notifier", WHO)
    world.wait(job)
    assert job.done and not job.error
    (env,) = world.walk_envs(fulfil=True)
    assert env["GH_TOKEN"] == "ghs_write" and env["PUBKIT_WALK_BY"] == WHO
    assert env["PUBSEC_WRITE_TOKEN_FILE"] == str(tmp_path / "write-token")
    argv = next(a for a, _, _ in world.runner.calls if "--fulfil" in a)
    assert argv[:6] == ["./pubdeploy", "walk", "--fulfil", "--yes", "--unit", "notifier"]
    last = world.engine.status.services["Sustenaut/notifier"].last
    assert last.documents[0]["needs"][0]["state"] == "ready"
    assert f"convergence: Sustenaut/notifier notifier: fulfil asked by {WHO}" in world.lines


def test_a_fulfil_that_prints_no_journal_says_why(world):
    world.runner.walks["Sustenaut/notifier"] = type(world.runner.walks["Sustenaut/notifier"])(2, "", "pubdeploy: no\n")
    job = world.engine.fulfil("Sustenaut/notifier", "notifier", WHO)
    world.wait(job)
    assert job.done and "printed no journal" in job.error


def test_a_fulfil_that_cannot_start_says_why(world):
    world.runner.failing["Sustenaut/notifier"] = "gone"
    job = world.engine.fulfil("Sustenaut/notifier", "notifier", WHO)
    world.wait(job)
    assert job.done and job.error.startswith("the fulfil could not start")
    assert world.engine.walking("Sustenaut/notifier").acquire(blocking=False)


@pytest.mark.parametrize(("repository", "unit", "said"), [
    ("Sustenaut/nothing", "x", "not been walked"), ("Sustenaut/notifier", "x", "no such unit")])
def test_a_fulfil_of_what_is_not_there_is_refused(world, repository, unit, said):
    assert said in world.engine.fulfil(repository, unit, WHO)


def test_no_fulfil_while_that_service_is_walked(world):
    world.engine.walking("Sustenaut/notifier").acquire()
    assert "is running" in world.engine.fulfil("Sustenaut/notifier", "notifier", WHO)


def test_an_engine_without_a_write_token_fulfils_nothing(tmp_path):
    w = World(tmp_path, fulfil=False)
    w.engine.round()
    assert "fulfils nothing" in w.engine.fulfil("Sustenaut/notifier", "notifier", WHO)


@pytest.fixture
def client(world):
    return TestClient(create_app(world.engine, Journals(Storage()), site=SITE, key=b"k" * 32))


def form_of(client):
    page = client.get("/services/Sustenaut/notifier", headers={"X-Forwarded-Email": WHO}).text
    import re
    return dict(re.findall(r'name="(unit|token)" value="([^"]+)"', page))


def test_the_page_offers_a_fulfil_to_the_signed_in_person_alone(client):
    assert "Fulfil the auto needs of notifier" in client.get(
        "/services/Sustenaut/notifier", headers={"X-Forwarded-Email": WHO}).text
    assert "Fulfil the" not in client.get("/services/Sustenaut/notifier").text


def test_a_fulfil_from_the_page_runs_and_its_page_says_how_it_went(client, world):
    world.runner.walk_prints("Sustenaut/notifier", journal(states={"k8s.secret:s": "ready"}))
    answer = client.post("/services/Sustenaut/notifier/fulfil", data=form_of(client), follow_redirects=False,
                         headers={"X-Forwarded-Email": WHO, "Origin": SITE})
    assert answer.status_code == 303
    job = next(iter(world.engine.status.fulfils.values()))
    world.wait(job)
    page = client.get(answer.headers["location"])
    assert "Done." in page.text and WHO in page.text and "Refresh" not in page.headers


def test_a_running_fulfils_page_reloads_itself(client, world):
    from convergence.engine import Fulfil
    world.engine.status.fulfils["x"] = Fulfil("x", "Sustenaut/notifier", "notifier", WHO, "now")
    page = client.get("/fulfils/x")
    assert page.headers["Refresh"] == "5" and "Running" in page.text
    world.engine.status.fulfils["x"].done, world.engine.status.fulfils["x"].error = True, "it broke"
    assert "it broke" in client.get("/fulfils/x").text
    assert client.get("/fulfils/nope").status_code == 404


@pytest.mark.parametrize(("headers", "change", "status"), [
    ({"Origin": SITE}, {}, 403),
    ({"X-Forwarded-Email": WHO, "Origin": "https://elsewhere.example"}, {}, 403),
    ({"X-Forwarded-Email": WHO, "Origin": SITE}, {"token": "0" * 64}, 403),
    ({"X-Forwarded-Email": "other@example.org", "Origin": SITE}, {}, 403),
])
def test_a_fulfil_without_the_person_this_site_or_their_form_is_refused(client, headers, change, status):
    data = {**form_of(client), **change}
    assert client.post("/services/Sustenaut/notifier/fulfil", data=data, headers=headers).status_code == status


def test_a_fulfil_the_engine_refuses_is_said(client, world):
    world.engine.walking("Sustenaut/notifier").acquire()
    answer = client.post("/services/Sustenaut/notifier/fulfil", data=form_of(client),
                         headers={"X-Forwarded-Email": WHO, "Origin": SITE})
    assert answer.status_code == 409 and "is running" in answer.text


POOL = "projects/1/locations/global/workloadIdentityPools/convergence"


def google_world(tmp_path):
    w = World(tmp_path)
    s = w.engine.settings
    import dataclasses
    w.engine.settings = dataclasses.replace(
        s, google_pool=POOL, google_reader="reader@p.iam.gserviceaccount.com",
        google_admin="admin@p.iam.gserviceaccount.com", google_read_token=tmp_path / "gread",
        google_write_token=tmp_path / "gwrite")
    w.engine.google = w.engine._google()  # noqa: SLF001
    w.engine.round()
    return w


def test_a_round_gets_the_read_paths_google_credential_alone(tmp_path):
    w = google_world(tmp_path)
    (env,) = w.walk_envs(fulfil=False)
    read = json.loads(Path(env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"]).read_text())
    assert read["audience"] == f"//iam.googleapis.com/{POOL}/providers/read"
    assert read["credential_source"] == {"file": str(tmp_path / "gread")}
    assert "reader@p.iam.gserviceaccount.com" in read["service_account_impersonation_url"]
    assert env["CLOUDSDK_CONFIG"] == str(tmp_path / "google" / "read" / "gcloud")
    assert env["CLOUDSDK_CORE_DISABLE_USAGE_REPORTING"] == "true"
    assert "gwrite" not in json.dumps(env) and "admin@" not in json.dumps(env)


def test_a_fulfil_gets_the_write_paths_google_credential_and_its_own_gcloud_directory(tmp_path):
    w = google_world(tmp_path)
    w.runner.walk_prints("Sustenaut/notifier", journal(states={"k8s.secret:s": "ready"}))
    job = w.engine.fulfil("Sustenaut/notifier", "notifier", WHO)
    w.wait(job)
    (env,) = w.walk_envs(fulfil=True)
    write = json.loads(Path(env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"]).read_text())
    assert write["audience"].endswith("/providers/write")
    assert write["credential_source"] == {"file": str(tmp_path / "gwrite")}
    assert env["CLOUDSDK_CONFIG"] == str(tmp_path / "google" / "write" / "gcloud")


def test_without_a_pool_no_walk_gets_a_google_credential(world):
    (env,) = world.walk_envs(fulfil=False)
    assert not any(k.startswith("CLOUDSDK_") for k in env)
