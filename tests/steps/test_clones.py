"""Steps for the clones (real git, on a local server), the walk's subprocess, and facts."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from convergence import clones as clones_module
from convergence import facts
from convergence.clones import CloneFailed, Clones, Ran, git_env, run
from convergence.journals import head
from convergence.walker import Credentials, walk

from .fakes import journal

scenarios("../features/clones.feature")

TOKEN = "ghs_secret_installation"


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    return {"tmp": tmp_path, "monkeypatch": monkeypatch}


@given(parsers.parse('a repository {slug} on a local git server, with pubkit.json "{text}"'))
def local_server(ctx: dict, slug: str, text: str) -> None:
    server = ctx["tmp"] / "server"
    source = ctx["tmp"] / "source"
    source.mkdir()
    git("init", "-q", "-b", "main", cwd=source)
    (source / "pubkit.json").write_text(text)
    git("add", ".", cwd=source)
    git("commit", "-qm", "one", cwd=source)
    (server / slug).parent.mkdir(parents=True)
    git("clone", "-q", "--bare", str(source), str(server / f"{slug}.git"), cwd=ctx["tmp"])
    ctx["source"] = source
    ctx["monkeypatch"].setattr(clones_module, "GITHUB", f"file://{server}")
    ctx["clones"] = Clones(ctx["tmp"] / "work")


@when(parsers.parse("the engine refreshes its clone of {slug}"))
def refresh(ctx: dict, slug: str) -> None:
    clones = ctx.get("clones") or Clones(ctx["tmp"] / "work", ctx["runner"], {})
    try:
        ctx["clone"] = clones.refresh(slug, TOKEN)
    except CloneFailed as e:
        ctx["error"] = str(e)


@then(parsers.parse('the clone\'s pubkit.json is "{text}"'))
def clone_has(ctx: dict, text: str) -> None:
    assert (ctx["clone"] / "pubkit.json").read_text() == text


@when(parsers.parse('the repository\'s pubkit.json becomes "{text}" and a stray file is left in the clone'))
def change(ctx: dict, text: str) -> None:
    (ctx["source"] / "pubkit.json").write_text(text)
    git("commit", "-qam", "two", cwd=ctx["source"])
    git("push", "-q", str(next((ctx["tmp"] / "server").rglob("*.git"))), "main",
        cwd=ctx["source"])
    (ctx["clone"] / "stray").write_text("x")


@then("the stray file is gone")
def stray_gone(ctx: dict) -> None:
    assert not (ctx["clone"] / "stray").exists()


@then("no file in the clone holds the token")
def no_token(ctx: dict) -> None:
    for path in ctx["clone"].rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes(), path


@when(parsers.parse('git\'s environment is made for the token "{token}"'))
def make_env(ctx: dict, token: str) -> None:
    ctx["env"] = git_env(token, {"PATH": "/bin"})


@then(parsers.parse('it sends "{header}" to github.com'))
def sends(ctx: dict, header: str) -> None:
    assert ctx["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert ctx["env"]["GIT_CONFIG_VALUE_0"] == header


@then("it never prompts")
def never_prompts(ctx: dict) -> None:
    assert ctx["env"]["GIT_TERMINAL_PROMPT"] == "0"


@when(parsers.parse('the engine runs "{command}" for at most 1 second'))
def runs(ctx: dict, command: str) -> None:
    ctx["ran"] = run(shlex.split(command), ctx["tmp"], {"PATH": os.environ["PATH"]}, 1)


@then(parsers.re(r'it ended with (?P<code>\d+) and said "(?P<said>.*)"$'))
def ended(ctx: dict, code: str, said: str) -> None:
    assert ctx["ran"].code == int(code) and ctx["ran"].stderr == said


@given(parsers.parse("git fails silently with {code:d}"))
def git_silent(ctx: dict, code: int) -> None:
    ctx["runner"] = lambda argv, cwd, env, timeout: Ran(code, "", "")


@then(parsers.parse('the refresh fails with "{text}"'))
def refresh_fails(ctx: dict, text: str) -> None:
    assert ctx["error"] == text


@given(parsers.parse("a clone of {slug} without ./pubdeploy"))
def without_pubdeploy(ctx: dict, slug: str) -> None:
    ctx["clone"] = ctx["tmp"] / slug
    ctx["clone"].mkdir(parents=True)
    ctx["slug"] = slug


@given(parsers.parse("a clone of {slug} whose walk prints '{text}'"))
def walk_prints(ctx: dict, slug: str, text: str) -> None:
    without_pubdeploy(ctx, slug)
    (ctx["clone"] / "pubdeploy").write_text("")
    ctx["runner"] = lambda argv, cwd, env, timeout: Ran(0, text, "")


@when("it is walked")
def walked(ctx: dict) -> None:
    ctx["walk"] = walk(ctx["slug"], ctx["clone"], None, "https://storage.test", Credentials("g", "s"), base={},
                       cache=ctx["tmp"], timeout=5, runner=ctx.get("runner", run))


@then(parsers.parse('the walk failed with "{text}"'))
def walk_failed(ctx: dict, text: str) -> None:
    assert not ctx["walk"].ok and ctx["walk"].error == text


@given(parsers.parse('a journal of {slug} with "{a}" {sa} and "{b}" {sb}'))
def a_journal(ctx: dict, slug: str, a: str, sa: str, b: str, sb: str) -> None:
    ctx["document"] = journal(slug, slug.split("/")[1], {a: sa, b: sb})


@when("its facts are made")
def make_facts(ctx: dict) -> None:
    ctx["rows"] = facts.rows([ctx["document"]])
    ctx["data"] = facts.encode(ctx["rows"])


@then(parsers.parse('there are {count:d} rows, and "{need}" is {state}, observed at the walk\'s start'))
def rows_are(ctx: dict, count: int, need: str, state: str) -> None:
    rows = {r["need"]: r for r in ctx["rows"]}
    assert len(ctx["rows"]) == count and rows[need]["state"] == state
    assert rows[need]["observed_at"] == rows[need]["walk_started_at"]


@then("the file decodes to the same rows, and the same rows make the same file")
def round_trip(ctx: dict) -> None:
    assert facts.decode(ctx["data"]) == ctx["rows"]
    assert facts.encode(list(ctx["rows"])) == ctx["data"]


@then(parsers.parse('its place is "{prefix}"'))
def its_place(ctx: dict, prefix: str) -> None:
    service = ctx["document"]["service"]
    place = facts.place(service["repository"], service["unit"], ctx["document"]["walk"]["started_at"], ctx["data"])
    assert place.startswith(prefix) and place.endswith(".avro")


def test_head_names_are_flat_lower_case_and_short() -> None:
    assert head("Sustenaut/release-controlplane", "release-schryer") == "sustenaut.release-controlplane.release-schryer"
    assert head("Some_Owner/Repo", "U" * 80) == ("some_owner.repo." + "u" * 80)[:64]
