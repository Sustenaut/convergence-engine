"""Steps for the pages, with the engine's status set by hand and storage faked."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

from convergence.clones import Clones
from convergence.config import settings
from convergence.engine import Engine, Service
from convergence.journals import Journals
from convergence.page import create_app
from convergence.walker import Walk

from .fakes import Storage, journal

scenarios("../features/page.feature")


class World:
    def __init__(self, tmp: Path) -> None:
        self.now = 1_000_000.0
        self.engine = Engine(settings({"CONVERGENCE_WORK": str(tmp)}), Clones(tmp), lambda: "t", None,
                             base_env={}, clock=lambda: self.now)
        self.storage = Storage()
        self.client = TestClient(create_app(self.engine, Journals(self.storage)))
        self.answer = None


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


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


@when(parsers.parse('I open "{path}"'))
def open_(world: World, path: str) -> None:
    world.answer = world.client.get(path)


@when(parsers.parse("I open the first walk of {slug}"))
def open_first(world: World, slug: str) -> None:
    world.answer = world.client.get(f"/services/{slug}/walks/{'a' * 64}")


@then(parsers.parse('the page shows "{text}"'))
def shows(world: World, text: str) -> None:
    assert world.answer.status_code == 200 and text in world.answer.text, world.answer.text


@then(parsers.parse('the page does not show "{text}"'))
def not_shows(world: World, text: str) -> None:
    assert text not in world.answer.text


@then(parsers.parse("the page shows {count:d} links to walks"))
def walk_links(world: World, count: int) -> None:
    assert len(re.findall(r'href="/services/[^"]+/walks/[0-9a-f]{64}"', world.answer.text)) == count


@then("the page is private and runs no script")
def private(world: World) -> None:
    assert world.answer.headers["Cache-Control"] == "private, no-store"
    assert world.answer.headers["Content-Security-Policy"].startswith("default-src 'none'")


@then(parsers.parse("the answer is {status:d}"))
def answer_is(world: World, status: int) -> None:
    assert world.answer.status_code == status, world.answer.text
