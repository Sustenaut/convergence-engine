"""A person's Fulfil: the write token reaches that walk alone, never a round's, and a
press is a request its person approves once the plan is walked."""

from __future__ import annotations

import dataclasses
import hashlib
from html import unescape
import hmac
import json
import re
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from convergence.approvals import Ask
from convergence.callers import Requester
from convergence.tokens import READ, Tokens

from .fakes import journal
from .world import REPOSITORY, SITE, WHO, World, person

scenarios("fulfil.feature")


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


@then("the rounds token may only read and is reused")
def the_rounds_token_may_only_read_and_is_reused():
    caller, now = Caller(), [1000.0]
    tokens = Tokens(caller, "1", "key", "9", sign=lambda *a: "jwt", clock=lambda: now[0])
    assert tokens.read() == "ghs_1" and tokens.read() == "ghs_1"
    assert json.loads(caller.asked[0].body) == {"permissions": READ}
    assert all(v == "read" for v in READ.values())
    now[0] += 46 * 60
    assert tokens.read() == "ghs_2"


@then("a fulfils token has the installations permissions and is minted each time")
def a_fulfils_token_has_the_installations_permissions_and_is_minted_each_time():
    caller = Caller()
    tokens = Tokens(caller, "1", "key", "9", sign=lambda *a: "jwt")
    assert tokens.write() == "ghs_1" and tokens.write() == "ghs_2"
    assert caller.asked[0].body is None


@then("a refused mint raises")
def a_refused_mint_raises():
    from integrations.calls import Refused
    with pytest.raises(Refused):
        Tokens(Caller(ok=False), "1", "key", "9", sign=lambda *a: "jwt").read()


POOL = "projects/1/locations/global/workloadIdentityPools/convergence"


@given("the engine has walked Sustenaut/notifier", target_fixture="world")
def walked(tmp_path: Path) -> World:
    w = World(tmp_path)
    w.engine.round()
    return w


@given("the engine without a write token has walked Sustenaut/notifier", target_fixture="world")
def walked_without_write(tmp_path: Path) -> World:
    w = World(tmp_path, fulfil=False)
    w.engine.round()
    return w


@given("the engine with a Google pool has walked Sustenaut/notifier", target_fixture="world")
def walked_with_google(tmp_path: Path) -> World:
    w = World(tmp_path)
    w.engine.settings = dataclasses.replace(
        w.engine.settings, google_pool=POOL, google_reader="reader@p.iam.gserviceaccount.com",
        google_admin="admin@p.iam.gserviceaccount.com", google_read_token=tmp_path / "gread",
        google_write_token=tmp_path / "gwrite")
    w.engine.google = w.engine._google()  # noqa: SLF001
    w.engine.round()
    return w


@given("the clone of Sustenaut/notifier fails")
def clone_fails(world: World) -> None:
    world.runner.failing[REPOSITORY] = "gone"


@given("a walk of Sustenaut/notifier is running")
def walk_running(world: World) -> None:
    world.engine.walking(REPOSITORY).acquire()
    world.approvals.lock_seconds = 0.2


@given("storage refuses every write")
def storage_refuses(world: World) -> None:
    world.storage.refuse_writes = True


def _makes_ready(world: World) -> None:
    world.runner.fulfil_prints(REPOSITORY, journal(states={"k8s.secret:s": "ready"}), stderr="k8s.secret:s: made\n")


@when(parsers.re(r'the person presses Fulfil for the unit "(?P<unit>[^"]+)" and the walk makes it ready'))
def press_ready(world: World, unit: str) -> None:
    _makes_ready(world)
    world.ask = world.approvals.press(REPOSITORY, unit, WHO)
    world.wait()


@when(parsers.re(r'the person presses Fulfil for the unit "(?P<unit>[^"]+)" and the walk prints no journal'))
def press_no_journal(world: World, unit: str) -> None:
    world.runner.fulfils[REPOSITORY] = type(world.runner.walks[REPOSITORY])(2, "", "pubdeploy: no\n")
    world.ask = world.approvals.press(REPOSITORY, unit, WHO)
    world.wait()


@when(parsers.re(r'the person presses Fulfil for the unit "(?P<unit>[^"]+)"'))
def press(world: World, unit: str) -> None:
    world.ask = world.approvals.press(REPOSITORY, unit, WHO)
    world.wait()


@when(parsers.re(r'the person presses Fulfil for the unit "(?P<unit>[^"]+)" of "(?P<repository>[^"]+)"'))
def press_of(world: World, unit: str, repository: str) -> None:
    world.ask = world.approvals.press(repository, unit, WHO)


@then(parsers.re(r'the request ends "(?P<state>[^"]+)"'))
def request_ends(world: World, state: str) -> None:
    assert world.ask.state == state, world.ask.error


@then(parsers.parse('the request ends "{state}", saying "{text}"'))
def request_ends_saying(world: World, state: str, text: str) -> None:
    assert world.ask.state == state and text in world.ask.error, world.ask.error


@then(parsers.parse('the press is refused, saying "{text}"'))
def press_refused(world: World, text: str) -> None:
    assert isinstance(world.ask, str) and text in world.ask


@then("the plan walk was read-only and the Fulfil walk had the write tokens as the person")
def plan_then_fulfil(world: World, tmp_path: Path) -> None:
    checks = [a for a in world.runner.walked("--unit") if "--fulfil" not in a]
    assert len(checks) == 2 and checks[0][:4] == ["./pubdeploy", "check", "--unit", "notifier"]
    (env,) = world.walk_envs(fulfil=True)
    assert env["GH_TOKEN"] == "ghs_write" and env["PUBKIT_WALK_BY"] == WHO
    assert env["PUBSEC_WRITE_TOKEN_FILE"] == str(tmp_path / "write-token")
    (argv,) = world.runner.walked("--fulfil")
    assert argv[:6] == ["./pubdeploy", "walk", "--fulfil", "--yes", "--unit", "notifier"]
    plan_envs = [env for argv, _, env in world.runner.calls if "check" in argv and "--unit" in argv]
    assert all(e["GH_TOKEN"] == "ghs_read" and "PUBSEC_WRITE_TOKEN_FILE" not in e for e in plan_envs)
    assert f"convergence: {REPOSITORY} notifier: fulfil approved by {WHO}" in world.lines
    assert world.ask.approver == WHO and world.ask.needs == [{"need": "k8s.secret:s", "state": "ready"}]


@then("the service page shows the need ready")
def service_ready(world: World) -> None:
    last = world.engine.status.services[REPOSITORY].last
    assert last.documents[0]["needs"][0]["state"] == "ready"


@then("the service can be walked again")
def walked_again(world: World) -> None:
    assert world.engine.walking(REPOSITORY).acquire(blocking=False)


@then("no Fulfil walk ran")
def no_fulfil(world: World) -> None:
    assert world.runner.walked("--fulfil") == [] and world.runner.walked("--unit") == []


def form_of(world: World, unit: str | None = None) -> dict[str, str]:
    page = world.client.get(f"/services/{REPOSITORY}", headers=person()).text
    if unit is None:
        return dict(re.findall(r'name="(unit|token)" value="([^"]+)"', page))
    # The token the page would make for a unit it shows no button for.
    return {"unit": unit, "token": hmac.new(b"k" * 32, "\n".join(("fulfil", WHO, REPOSITORY, unit)).encode(),
                                            hashlib.sha256).hexdigest()}


VIEWERS = {"the person": person(), "no one signed in": {}, "the person without the role": person(role=False),
           "the person, with the role on the second of two group lines": None}


@when(parsers.re(r"(?P<viewer>the person|no one signed in|the person without the role|"
                 r"the person, with the role on the second of two group lines) opens the service page"))
def opens_service(world: World, viewer: str) -> None:
    headers = VIEWERS[viewer]
    if headers is None:  # oauth2-proxy adds one line per group
        headers = [("X-Forwarded-Email", WHO), ("X-Forwarded-Groups", "people"), ("X-Forwarded-Groups", "convergence")]
    world.answer = world.client.get(f"/services/{REPOSITORY}", headers=headers)


@then("Fulfil notifier can be pressed, at the top and on the unit")
def fulfil_pressable(world: World) -> None:
    text = world.answer.text
    assert text.count(f'<form method="post" action="/services/{REPOSITORY}/fulfil">') == 2
    assert text.count(">Fulfil notifier</button>") == 2 and " disabled" not in text
    assert text.index("<div class=actions") < text.index("<h2>Unit notifier</h2>")


@then(parsers.parse('Fulfil notifier is shown disabled, at the top and on the unit, saying "{why}"'))
def fulfil_disabled(world: World, why: str) -> None:
    text = world.answer.text
    assert "/fulfil\">" not in text and 'name="token"' not in text
    for slot in ("top-0", "unit-0"):
        assert re.search(rf'<button type="button" disabled title="[^"]*{re.escape(why)}[^"]*" '
                         rf'aria-describedby="why-{slot}">Fulfil notifier</button>', unescape(text))
        assert why in unescape(re.search(rf'id="why-{slot}">([^<]*)<', text).group(1))
    if world.engine.walking(REPOSITORY).locked():
        world.engine.walking(REPOSITORY).release()


@given("nothing more")
def nothing_more() -> None:
    pass


@given("every need is ready")
def every_ready(world: World) -> None:
    world.runner.walk_prints(REPOSITORY, journal(states={"k8s.secret:s": "ready"}))
    world.engine.round()


@given("the need left is a proposal")
def need_is_proposal(world: World) -> None:
    world.runner.walk_prints(REPOSITORY, journal(states={"github.secret:S": "missing"},
                                                 grades={"github.secret:S": "propose"}), code=1)
    world.engine.round()


@given("a request to fulfil the unit is open")
def request_open(world: World) -> None:
    world.form = form_of(world)
    ask = Ask("open", "fulfil", Requester("person", WHO, actor=WHO), REPOSITORY, "notifier", world.now,
              world.now + 3600)
    world.approvals._take(ask, "awaiting", plan=[], plan_sha256="0" * 64)  # noqa: SLF001 - held open


@given("a walk of Sustenaut/notifier is running now")
def walk_running_now(world: World) -> None:
    world.engine.walking(REPOSITORY).acquire()


@when("the person posts the Fulfil form they had")
def posts_form_had(world: World) -> None:
    world.answer = world.client.post(f"/services/{REPOSITORY}/fulfil", data=world.form, headers=person())


@when("the person posts the Fulfil form and the walk makes it ready")
def post_form(world: World) -> None:
    _makes_ready(world)
    world.answer = world.client.post(f"/services/{REPOSITORY}/fulfil", data=form_of(world), follow_redirects=False,
                                     headers=person())
    world.wait()


@when(parsers.parse('the person posts the Fulfil form for the unit "{unit}"'))
def post_form_unit(world: World, unit: str) -> None:
    world.answer = world.client.post(f"/services/{REPOSITORY}/fulfil", data=form_of(world, unit), headers=person())


@then("the answer sends them to the request's page")
def sent_to_request(world: World) -> None:
    assert world.answer.status_code == 303 and world.answer.headers["location"].startswith("/approvals/")


@then("the request's page says it is done, approved by the person")
def request_page_done(world: World) -> None:
    page = world.client.get(world.answer.headers["location"], headers=person())
    assert "<span class=ready>done</span>" in page.text and "Refresh" not in page.headers
    assert "<td><span class=ready>approved</span><td>" in page.text and WHO in page.text
    assert "k8s.secret:s: made" in page.text and "<code>k8s.secret:s</code>" in page.text
    assert 'name="decision"' not in page.text and "it no longer awaits a decision" in page.text


@then("the old Fulfil link leads to the request's page")
def old_link(world: World) -> None:
    id_ = world.answer.headers["location"].rsplit("/", 1)[1]
    answer = world.client.get(f"/fulfils/{id_}", follow_redirects=False)
    assert answer.status_code == 303 and answer.headers["location"] == f"/approvals/{id_}"


ASKERS = {
    "no one signed in": {"Origin": SITE},
    "the person without the role": person(role=False),
    "the person, from another site": person(origin="https://elsewhere.example"),
    "another person": person("other@example.org"),
    "the person": person(),
}
FORMS = {"their form": {}, "a form with another token": {"token": "0" * 64}}


@when(parsers.parse("{asker} posts a fulfil with {form}"))
def posts_fulfil(world: World, asker: str, form: str) -> None:
    data = {**form_of(world), **FORMS[form]}
    world.answer = world.client.post(f"/services/{REPOSITORY}/fulfil", data=data, headers=ASKERS[asker])


@then(parsers.re(r"the page answers (?P<status>\d+)"), converters={"status": int})
def page_answers(world: World, status: int) -> None:
    assert world.answer.status_code == status


@then(parsers.parse('the page answers {status:d} saying "{text}"'))
def page_answers_saying(world: World, status: int, text: str) -> None:
    assert world.answer.status_code == status and text in world.answer.text


@then("a rounds walk holds no write token")
def a_rounds_walk_holds_no_write_token(world: World) -> None:
    (env,) = world.walk_envs(fulfil=False)
    assert "PUBSEC_WRITE_TOKEN_FILE" not in env and env["GH_TOKEN"] == "ghs_read"
    assert "bws-write" not in json.dumps(env)


@then("a round gets the read paths google credential alone")
def a_round_gets_the_read_paths_google_credential_alone(world: World, tmp_path: Path) -> None:
    (env,) = world.walk_envs(fulfil=False)
    read = json.loads(Path(env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"]).read_text())
    assert read["audience"] == f"//iam.googleapis.com/{POOL}/providers/read"
    assert read["credential_source"] == {"file": str(tmp_path / "gread")}
    assert "reader@p.iam.gserviceaccount.com" in read["service_account_impersonation_url"]
    assert env["CLOUDSDK_CONFIG"] == str(tmp_path / "google" / "read" / "gcloud")
    assert env["CLOUDSDK_CORE_DISABLE_USAGE_REPORTING"] == "true"
    assert "gwrite" not in json.dumps(env) and "admin@" not in json.dumps(env)
    assert "PUBSEC_GCP_KEYS_GRANTED_IN" not in env


@then("the fulfil got the write paths google credential and its own gcloud directory")
def the_fulfil_got_the_write_path(world: World, tmp_path: Path) -> None:
    (env,) = world.walk_envs(fulfil=True)
    write = json.loads(Path(env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"]).read_text())
    assert write["audience"].endswith("/providers/write")
    assert write["credential_source"] == {"file": str(tmp_path / "gwrite")}
    assert env["CLOUDSDK_CONFIG"] == str(tmp_path / "google" / "write" / "gcloud")
    assert env["PUBSEC_GCP_KEYS_GRANTED_IN"].endswith("infra/convergence-keys.tf")


@then("no walk gets a google credential")
def no_google(world: World) -> None:
    (env,) = world.walk_envs(fulfil=False)
    assert not any(k.startswith("CLOUDSDK_") for k in env)
