"""Steps for the engine's rounds, with git, pubdeploy and storage faked."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from convergence import facts
from convergence.clones import Clones, Ran
from convergence.config import settings
from convergence.engine import Engine, Service, prepare

from .fakes import Runner, Storage, journal

scenarios("../features/engine.feature")


class World:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        (tmp / "storage-token").write_text("storage-token\n")
        self.env = {"CONVERGENCE_STORAGE_URL": "https://storage.test",
                    "CONVERGENCE_STORAGE_TOKEN_FILE": str(tmp / "storage-token"), "CONVERGENCE_WORK": str(tmp)}
        self.runner, self.storage, self.lines = Runner(), Storage(), []
        self.buffer: Storage | None = self.storage
        self.token_error = False
        self.now = 1_000_000.0
        self._engine: Engine | None = None
        self.started: bool | None = None

    def token(self) -> str:
        if self.token_error:
            raise RuntimeError("refused")
        return "ghs_installation"

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            from convergence.walker import walk

            def walker(*args, **kwargs):
                return walk(*args, **kwargs, runner=self.runner)

            self._engine = Engine(settings(self.env), Clones(self.tmp, self.runner, {"PATH": "/bin"}), self.token,
                                  self.buffer, base_env={"PATH": "/bin", "HOME": "/work", "SECRET": "engine-only"},
                                  walker=walker, log=self.lines.append, clock=lambda: self.now)
        return self._engine

    def walk_of(self, repository: str) -> list[str]:
        return next(argv for argv, cwd, _ in self.runner.calls
                    if argv[0] != "git" and f"{cwd.parent.name}/{cwd.name}" == repository)

    def env_of(self, repository: str) -> dict:
        return next(env for argv, cwd, env in self.runner.calls
                    if argv[0] != "git" and f"{cwd.parent.name}/{cwd.name}" == repository)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


@given(parsers.parse('the state repository {slug} gives the role deploy to "{names}"'))
def state_gives(world: World, slug: str, names: str) -> None:
    repos = {n.strip(): {"board": True, "deploy": True} for n in names.split(",")}
    repos["pubkit"] = {"board": True}
    world.runner.states[slug] = json.dumps({"owner": "Sustenaut", "repos": repos})


@given(parsers.parse("the zones repositories are '{text}'"))
def zones_are(world: World, text: str) -> None:
    world.env["CONVERGENCE_ZONES"] = text


@given(parsers.re(r'the walk of (?P<slug>\S+) finds "(?P<a>[^"]+)" (?P<sa>\w+)( and "(?P<b>[^"]+)" (?P<sb>\w+))?$'))
def walk_finds(world: World, slug: str, a: str, sa: str, b: str | None, sb: str | None) -> None:
    states = {a: sa, **({b: sb} if b else {})}
    world.runner.walk_prints(slug, journal(slug, slug.split("/")[1], states), code=0 if sb in (None, "ready") else 1)


@given(parsers.parse("the walk of {slug} finds no needs"))
def walk_finds_none(world: World, slug: str) -> None:
    world.runner.walk_prints(slug, journal(slug, slug.split("/")[1], {}))


@given(parsers.parse('the walk of {slug} finds units "{units}"'))
def walk_units(world: World, slug: str, units: str) -> None:
    world.runner.walk_prints(slug, [journal(slug, u.strip(), {f"k8s.namespace:{u.strip()}": "ready"})
                                    for u in units.split(",")])


@given(parsers.parse('the walk of {slug} prints nothing and says "{said}"'))
def walk_nothing(world: World, slug: str, said: str) -> None:
    world.runner.walks[slug] = Ran(2, "", f"{said}\n")


@given(parsers.parse('git cannot clone {slug}: "{said}"'))
def git_fails(world: World, slug: str, said: str) -> None:
    world.runner.failing[slug] = said


@given("a Bitwarden read token")
def bitwarden(world: World) -> None:
    (world.tmp / "bws").write_text("bws-read-token\n")
    world.env["CONVERGENCE_BITWARDEN_TOKEN_FILE"] = str(world.tmp / "bws")


@given("storage refuses everything")
def storage_refuses(world: World) -> None:
    world.storage.refuse = "refused"


@given("the engine has no buffer")
def no_buffer(world: World) -> None:
    world.buffer = None


@given("GitHub refuses the token")
def github_refuses(world: World) -> None:
    world.token_error = True


@given("the storage token file is missing")
def no_storage_token(world: World) -> None:
    (world.tmp / "storage-token").unlink()


@given("a round has run")
@when("a round runs")
def round_runs(world: World) -> None:
    world.started = world.engine.round()


@given("a round is running")
def round_running(world: World) -> None:
    world.engine._running.acquire()  # noqa: SLF001 - as a round in another thread holds it


@when("a round is asked for")
def round_asked(world: World) -> None:
    world.started = world.engine.round()


@then("it is not started")
def not_started(world: World) -> None:
    assert world.started is False
    world.engine._running.release()  # noqa: SLF001


@then(parsers.parse("{slug}'s last walk has {count:d} needs"))
def last_walk_needs(world: World, slug: str, count: int) -> None:
    last = world.engine.status.services[slug].last
    assert last is not None and len(last.documents[0]["needs"]) == count


@then(parsers.parse("{slug}'s last walk has {count:d} journals"))
def last_walk_journals(world: World, slug: str, count: int) -> None:
    assert len(world.engine.status.services[slug].last.documents) == count


@then(parsers.parse("{slug} has no last walk"))
def no_last(world: World, slug: str) -> None:
    assert world.engine.status.services[slug].last is None


@then(parsers.parse('{slug}\'s failure says "{text}"'))
def failure_says(world: World, slug: str, text: str) -> None:
    assert text in world.engine.status.services[slug].failure, world.engine.status.services[slug].failure


@then(parsers.parse('the log says "{line}"'))
def log_says(world: World, line: str) -> None:
    assert line in world.lines, world.lines


@then(parsers.parse("storage's buffer holds the facts of {count:d} walks"))
def buffer_holds(world: World, count: int) -> None:
    assert len(world.storage.buffer) == count
    assert all(p.startswith(facts.FOLDER) for p in world.storage.buffer)


@then(parsers.parse('the facts of {slug} say "{need}" is {state}'))
def facts_say(world: World, slug: str, need: str, state: str) -> None:
    rows = [r for data in world.storage.buffer.values() for r in facts.decode(data) if r["repository"] == slug]
    assert {r["need"]: r["state"] for r in rows}[need] == state


@then(parsers.parse("{slug} was cloned"))
def was_cloned(world: World, slug: str) -> None:
    assert any(argv[:2] == ["git", "clone"] and argv[-2] == f"https://github.com/{slug}.git"
               for argv, _, _ in world.runner.calls)


@then(parsers.parse('{slug} was walked with "{command}"'))
def walked_with(world: World, slug: str, command: str) -> None:
    assert world.walk_of(slug) == command.split(), world.walk_of(slug)
    env = world.env_of(slug)
    assert "SECRET" not in env and env["PUBKIT_PLANE"] == "1" and env["GH_TOKEN"] == "ghs_installation"
    assert env["PUBKIT_STORAGE_TOKEN"] == "storage-token" and env["PUBKIT_WALK_BY"] == "schedule"
    assert all("ghs_" not in a and "token" not in a for argv, _, _ in world.runner.calls for a in argv)


@then(parsers.parse("{slug}'s walk had BWS_ACCESS_TOKEN"))
def had_bws(world: World, slug: str) -> None:
    assert world.env_of(slug)["BWS_ACCESS_TOKEN"] == "bws-read-token"


@then(parsers.parse('the round\'s problem is "{text}"'))
def round_problem(world: World, text: str) -> None:
    assert world.engine.status.problem == text, world.engine.status.problem


@given(parsers.parse("the engine started {started:d} minutes ago and its last round ended {ended} minutes ago"))
def health_times(world: World, started: int, ended: str) -> None:
    engine = world.engine
    engine.started = world.now - started * 60
    engine.status.round_ended = None if ended == "none" else world.now - int(ended) * 60


@then(parsers.parse("the engine is {health}"))
def engine_health(world: World, health: str) -> None:
    assert world.engine.healthy() is (health == "healthy")


def test_rounds_do_not_overlap_across_threads(tmp_path: Path) -> None:
    w = World(tmp_path)
    w.runner.states["Sustenaut/release-state"] = json.dumps({"owner": "Sustenaut", "repos": {}})
    w.engine._running.acquire()  # noqa: SLF001
    results = []
    t = threading.Thread(target=lambda: results.append(w.engine.round()))
    t.start()
    t.join()
    w.engine._running.release()  # noqa: SLF001
    assert results == [False]


@given(parsers.parse("a round listed {count:d} services"))
def listed(world: World, count: int) -> None:
    world.engine.status.services = {f"Sustenaut/s{k}": Service(f"Sustenaut/s{k}") for k in range(count)}


@when("the work directory is prepared")
def prepared(world: World) -> None:
    prepare(world.tmp / "fresh")


@then(parsers.parse('it has "{parts}"'))
def has_parts(world: World, parts: str) -> None:
    assert all((world.tmp / "fresh" / p.strip()).is_dir() for p in parts.split(","))
