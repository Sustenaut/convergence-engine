"""Steps for the pages, with the engine's status set by hand and storage faked."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

from convergence import page
from convergence.approvals import Approvals, Ledger
from convergence.clones import Clones
from convergence.config import settings
from convergence.engine import Engine, Service
from convergence.journals import Journals
from convergence.page import create_app
from convergence.walker import Walk

from .fakes import Storage, journal
from .world import client_of

scenarios("../features/page.feature")


class World:
    def __init__(self, tmp: Path) -> None:
        self.now = 1_000_000.0
        self.engine = Engine(settings({"CONVERGENCE_WORK": str(tmp)}), Clones(tmp), lambda: "t", None,
                             base_env={}, clock=lambda: self.now)
        self.storage = Storage()
        self.client = TestClient(create_app(self.engine, Journals(self.storage),
                                            Approvals(self.engine, Ledger(self.storage), log=lambda line: None)))


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


@pytest.fixture
def http_client(world: World):
    return client_of(world)


def _parse(found: str) -> tuple[dict, dict, dict]:
    states, grades, fixes = {}, {}, {}
    for part in re.findall(r'"([^"]+)" (\w+)(?: as (\w+))?(?: with fix "([^"]*)")?', found):
        need, state, grade, fix = part
        states[need] = state
        if grade:
            grades[need] = grade
        if fix:
            fixes[need] = fix
    return states, grades, fixes


@given(parsers.parse("the engine walked {slug} and found {found}"))
def walked(world: World, slug: str, found: str) -> None:
    states, grades, fixes = _parse(found)
    document = journal(slug, slug.split("/")[1], states, grades, fixes)
    walk = Walk(slug, "2026-11-10T06:00:00Z", 2.5, [document])
    world.engine.status.services[slug] = Service(slug, walk, "", walk.started_at)


@given(parsers.parse('storage\'s log for the head "{head}" holds {count:d} walks'))
def log_holds(world: World, head: str, count: int) -> None:
    world.storage.logs[head] = [{"hash": c * 64, "time": f"2026-11-10T0{k}:00:00Z", "principal": "k8s:convergence/app"}
                                for k, c in enumerate("ab"[:count])] + [{"hash": "nope", "time": "", "principal": ""}]
    for entry in world.storage.logs[head][:count]:
        world.storage.files[entry["hash"]] = json.dumps(journal()).encode()


@given(parsers.parse('storage has no log for the head "{head}"'))
def no_log(world: World, head: str) -> None:
    world.storage.logs.pop(head, None)


@given(parsers.parse("storage's file for the first walk is {slug}'s"))
def other_file(world: World, slug: str) -> None:
    world.storage.files["a" * 64] = json.dumps(journal(slug)).encode()


@given(parsers.re(r"^storage (?P<how>answers|refuses everything)$"))
def storage_how(world: World, how: str) -> None:
    if how == "refuses everything":
        world.storage.refuse = "refused"


@given(parsers.parse('the latest try of {slug} failed: "{text}"'))
def failed(world: World, slug: str, text: str) -> None:
    world.engine.status.services[slug].failure = text


@given(parsers.parse("the engine lists {slug} but has not walked it"))
def listed(world: World, slug: str) -> None:
    world.engine.status.services[slug] = Service(slug)


@given(parsers.parse('the round\'s problem is "{text}"'))
def problem(world: World, text: str) -> None:
    world.engine.status.problem = text


@given(parsers.parse("the engine is {health}"))
def health(world: World, health: str) -> None:
    if health == "unhealthy":
        world.now += 10 * 3600


@then(parsers.parse("the answer links to {count:d} walks"))
def walk_links(ctx, count: int) -> None:
    assert len(re.findall(r'href="/services/[^"]+/walks/[0-9a-f]{64}"', ctx.answer.text)) == count


def _pinned() -> dict:
    return json.loads((page.THEME_DIR.parent / "theme.json").read_text())


@when("theme.json is read")
def theme_json(ctx) -> None:
    ctx.attempt(_pinned)


@when("theme.json's version is read")
def theme_version(ctx) -> None:
    ctx.attempt(lambda: _pinned()["version"])


@when("every file theme.json pins is fetched")
def theme_fetched(ctx, world: World) -> None:
    def unlike_pin() -> list[str]:
        files = _pinned()["files"]
        assert files, "theme.json pins no file"
        unlike = []
        for name, sha in ((f["path"], f["sha256"]) for f in files):
            answer = world.client.get(f"/theme/{name}")
            if answer.status_code != 200 or hashlib.sha256(answer.content).hexdigest() != sha:
                unlike.append(name)
        return unlike
    ctx.attempt(unlike_pin)


@given("a copy of the theme whose base.css differs from its pin")
def theme_copy(tmp_path: Path, monkeypatch) -> None:
    copy = tmp_path / "convergence"
    shutil.copytree(page.THEME_DIR, copy / "theme")
    shutil.copy(page.THEME_DIR.parent / "theme.json", copy / "theme.json")
    (copy / "theme" / "base.css").write_text("body{}")
    monkeypatch.setattr(page, "THEME_DIR", copy / "theme")


@when("the theme is loaded")
def theme_loaded(ctx) -> None:
    ctx.attempt(page._theme)  # noqa: SLF001 - what the app serves
