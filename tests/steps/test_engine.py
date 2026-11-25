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
        self.runner, self.storage = Runner(), Storage()
        self.buffer: Storage | None = self.storage
        self.token_error = False
        self.now = 1_000_000.0
        self._engine: Engine | None = None

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
                                  walker=walker, log=print, clock=lambda: self.now)
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
    repos["pubrules"] = {"board": True}
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
def round_ran(world: World) -> None:
    world.engine.round()


@when("a round runs")
@when("a round is asked for")
def round_runs(ctx, world: World) -> None:
    ctx.attempt(world.engine.round)


@given("a round is running")
def round_running(ctx, world: World) -> None:
    running = world.engine._running  # noqa: SLF001 - as a round in another thread holds it
    running.acquire()
    ctx.cleanup(running.release)


@when("a round is asked for from another thread")
def round_other_thread(ctx, world: World) -> None:
    def other() -> bool:
        results: list[bool] = []
        thread = threading.Thread(target=lambda: results.append(world.engine.round()))
        thread.start()
        thread.join()
        return results[0]
    ctx.attempt(other)


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
    assert "SECRET" not in env and env["PUBRULES_PLANE"] == "1" and env["GH_TOKEN"] == "ghs_installation"
    assert env["PUBRULES_STORAGE_TOKEN"] == "storage-token" and env["PUBRULES_WALK_BY"] == "schedule"
    assert env["PUBRULES_PLANE"] == "1" and env["PUBRULES_STORAGE_TOKEN"] == "storage-token"
    assert env["PUBRULES_WALK_BY"] == "schedule"
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


@when("the engine's health is asked")
def health_asked(ctx, world: World) -> None:
    ctx.attempt(world.engine.healthy)


@given(parsers.parse("a round listed {count:d} services"))
def listed(world: World, count: int) -> None:
    world.engine.status.services = {f"Sustenaut/s{k}": Service(f"Sustenaut/s{k}") for k in range(count)}


@when(parsers.parse('the work directory "{name}" is prepared'))
def prepared(ctx, name: str) -> None:
    ctx.attempt(lambda: prepare(ctx.directory / name))


# --- The services listed at start ---------------------------------------------------------------


@given(parsers.parse("{repository}'s pubrules.json deploys the namespace \"{namespace}\" and the units \"{units}\""))
def deploys(world: World, repository: str, namespace: str, units: str) -> None:
    world.runner.configs[repository] = json.dumps(
        {"deploy": {"namespace": namespace, "units": {u: {"namespace": u} for u in units.split(", ")}}})


@given(parsers.re(r"(?P<repository>\S+)'s pubrules\.json is \"(?P<text>.*)\"$"))
def config_is(world: World, repository: str, text: str) -> None:
    world.runner.configs[repository] = text


@given("the engine starts and lists the services")
@when("the engine starts and lists the services")
def lists(ctx, world: World) -> None:
    ctx.attempt(world.engine.listing)


@then(parsers.parse('it lists {repository} with the units "{units}"'))
def listed_units(world: World, repository: str, units: str) -> None:
    assert world.engine.status.services[repository].units == units.split(", ")


@then(parsers.parse("it lists {repository} with no units"))
def listed_none(world: World, repository: str) -> None:
    assert world.engine.status.services[repository].units == []


@then("nothing was walked")
def nothing_walked(world: World) -> None:
    assert not [c for c in world.runner.calls if c[0][0].startswith("./pubdeploy")]
    assert all(s.last is None for s in world.engine.status.services.values())


@then(parsers.parse('the engine\'s problem reads "{text}"'))
def problem_reads(world: World, text: str) -> None:
    assert text in world.engine.status.problem, world.engine.status.problem
