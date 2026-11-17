"""Steps for the pages, with the engine's status set by hand and storage faked."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

from convergence.approvals import Approvals, Ledger
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
        self.client = TestClient(create_app(self.engine, Journals(self.storage),
                                            Approvals(self.engine, Ledger(self.storage), log=lambda line: None)))
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
    policy = world.answer.headers["Content-Security-Policy"]
    assert policy.startswith("default-src 'none'") and "script-src" not in policy
    assert "<script" not in world.answer.text


@then(parsers.parse("the answer is {status:d}"))
def answer_is(world: World, status: int) -> None:
    assert world.answer.status_code == status, world.answer.text


@then("the page links the theme's fonts, tokens and base styles")
def links_theme(world: World) -> None:
    for name in ("fonts.css", "tokens.css", "base.css"):
        assert f'<link rel="stylesheet" href="/theme/{name}">' in world.answer.text
    assert "style-src 'self' 'unsafe-inline'; font-src 'self'" in world.answer.headers["Content-Security-Policy"]


@then("every theme file is served as theme.json pins it")
def theme_served(world: World) -> None:
    import hashlib
    from convergence import page
    pinned = json.loads((page.THEME_DIR.parent / "theme.json").read_text())
    assert pinned["repository"] == "Sustenaut/theme" and pinned["version"].startswith("v")
    for name, sha in ((f["path"], f["sha256"]) for f in pinned["files"]):
        answer = world.client.get(f"/theme/{name}")
        assert answer.status_code == 200 and hashlib.sha256(answer.content).hexdigest() == sha
    assert world.client.get("/theme/fonts/nunito-latin-wght-normal.woff2").headers["content-type"] == "font/woff2"
    assert world.client.get("/theme/tokens.css").headers["content-type"].startswith("text/css")


@then("a file that is not the theme's is 404")
def theme_404(world: World) -> None:
    assert world.client.get("/theme/../theme.json").status_code == 404
    assert world.client.get("/theme/nope.css").status_code == 404


@then("a theme file whose bytes differ from theme.json is not served")
def theme_differs(world: World, tmp_path: Path, monkeypatch) -> None:
    import shutil
    from convergence import page
    copy = tmp_path / "convergence"
    shutil.copytree(page.THEME_DIR, copy / "theme")
    shutil.copy(page.THEME_DIR.parent / "theme.json", copy / "theme.json")
    (copy / "theme" / "base.css").write_text("body{}")
    monkeypatch.setattr(page, "THEME_DIR", copy / "theme")
    served = page._theme()  # noqa: SLF001
    assert "base.css" not in served and "tokens.css" in served
