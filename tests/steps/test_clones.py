"""Steps for the clones (real git, on pubtest's bare remotes), the walk's subprocess, and facts."""

from __future__ import annotations

import dataclasses
import os
import shlex
from pathlib import Path

from pubtest.steps.git import git
from pytest_bdd import given, parsers, scenarios, then, when

from convergence import clones as clones_module
from convergence import facts
from convergence.clones import Clones, Ran, git_env, run
from convergence.journals import head
from convergence.walker import Credentials, walk

from .fakes import journal

scenarios("../features/clones.feature")

TOKEN = "ghs_secret_installation"


@given("the engine clones from the bare remotes")
def clones_from_remotes(ctx, tmp_path: Path, monkeypatch) -> None:
    # pubtest's `a bare remote "OWNER/NAME"` keeps it at pubtest-remotes/OWNER/NAME.git, as GitHub's URL has it.
    monkeypatch.setattr(clones_module, "GITHUB", f"file://{tmp_path / 'pubtest-remotes'}")
    ctx.clones = Clones(ctx.directory / "work")


@when(parsers.parse("the engine refreshes its clone of {slug}"))
def refresh(ctx, tmp_path: Path, slug: str) -> None:
    clones = getattr(ctx, "clones", None) or Clones(tmp_path / "work", ctx.runner, {})
    ctx.attempt(lambda: clones.refresh(slug, TOKEN))


@when(parsers.parse('the change is pushed to "{remote}"'))
def pushed(ctx, remote: str) -> None:
    ctx.attempt(lambda: (git(ctx, "commit", "-qam", "two"), git(ctx, "push", "-q", remote, "HEAD")))


@when(parsers.parse('git\'s environment is made for the token "{token}"'))
def make_env(ctx, token: str) -> None:
    ctx.attempt(lambda: git_env(token, {"PATH": "/bin"}))


@when(parsers.parse('the engine runs "{command}" for at most 1 second'))
def runs(ctx, tmp_path: Path, command: str) -> None:
    ctx.attempt(lambda: dataclasses.asdict(run(shlex.split(command), tmp_path, {"PATH": os.environ["PATH"]}, 1)))


@given(parsers.parse("git fails silently with {code:d}"))
def git_silent(ctx, code: int) -> None:
    ctx.runner = lambda argv, cwd, env, timeout: Ran(code, "", "")


@given(parsers.parse("a clone of {slug} without ./pubdeploy"))
def without_pubdeploy(ctx, tmp_path: Path, slug: str) -> None:
    ctx.clone = tmp_path / slug
    ctx.clone.mkdir(parents=True)
    ctx.slug = slug


@given(parsers.parse("a clone of {slug} whose walk prints '{text}'"))
def walk_prints(ctx, tmp_path: Path, slug: str, text: str) -> None:
    without_pubdeploy(ctx, tmp_path, slug)
    (ctx.clone / "pubdeploy").write_text("")
    ctx.runner = lambda argv, cwd, env, timeout: Ran(0, text, "")


@when("it is walked")
def walked(ctx, tmp_path: Path) -> None:
    ctx.attempt(lambda: walk(ctx.slug, ctx.clone, None, "https://storage.test", Credentials("g", "s"), base={},
                             cache=tmp_path, timeout=5, runner=getattr(ctx, "runner", run)))


@given(parsers.parse('a journal of {slug} with "{a}" {sa} and "{b}" {sb}'))
def a_journal(ctx, slug: str, a: str, sa: str, b: str, sb: str) -> None:
    ctx.document = journal(slug, slug.split("/")[1], {a: sa, b: sb})


@when("its facts are made")
def make_facts(ctx) -> None:
    ctx.attempt(lambda: facts.rows([ctx.document]))


@when("its facts are made into a file")
def make_file(ctx) -> None:
    ctx.rows = facts.rows([ctx.document])
    ctx.attempt(lambda: facts.encode(ctx.rows))


@then("the file decodes to the same rows, and the same rows make the same file")
def round_trip(ctx) -> None:
    assert facts.decode(ctx.result) == ctx.rows
    assert facts.encode(list(ctx.rows)) == ctx.result


@when("its facts' place is found")
def its_place(ctx) -> None:
    service = ctx.document["service"]
    data = facts.encode(facts.rows([ctx.document]))
    ctx.attempt(lambda: facts.place(service["repository"], service["unit"], ctx.document["walk"]["started_at"], data))


@when(parsers.parse('the head of "{repository}" unit "{unit}" is named'))
def head_named(ctx, repository: str, unit: str) -> None:
    ctx.attempt(lambda: head(repository, unit))
