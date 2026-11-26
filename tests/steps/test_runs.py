"""Steps for live runs: the engine, its requests and pages as world.py makes them, with
`pubdeploy machine` faked by the runner and each need's walk by a fake process."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pubdeploy import machine as need_machine
from pytest_bdd import given, parsers, scenarios, then, when

from convergence.approvals import Ledger
from convergence.clones import Ran
from convergence.executor import Executor, Popen
from convergence.journals import Journals
from convergence.page import create_app
from convergence.runs import HEAD, Runs

from .fakes import Storage, journal
from .world import REPOSITORY, SITE, WHO, World, answered, person

scenarios("runs.feature")
scenarios("runpage.feature")

WARNING = "it migrates the database"


class Proc:
    """A need's walk: what it prints once it ends, and how it ends."""

    def __init__(self, spawn: Spawn, need: str, fulfil: bool, outcome):
        self.spawn, self.need, self.fulfil, self.outcome = spawn, need, fulfil, outcome
        self.terminated = False
        self.budgeted = False

    def printed(self, state: str, detail: str = "", need: str | None = None):
        document = journal(states={need or self.need: state})
        if detail:
            document["needs"][0]["details"] = [detail]
        return 0, json.dumps(document), ""

    def poll(self, timeout: float):
        if self.terminated:
            return -15, "", "terminated"
        if self.budgeted:  # pubdeploy's walk on SIGUSR1: the step ends unknown, its journal kept
            return self.printed("unknown", "its budget ended at the stop")
        kind, *rest = self.outcome
        if kind == "gate":
            return self.printed("ready") if self.spawn.gates[self.need].wait(timeout) else None
        if kind == "barrier":
            try:
                self.spawn.barrier.wait(5)
            except threading.BrokenBarrierError:
                return 1, "", "alone"
            return self.printed("ready")
        if kind == "hang":  # it never ends by itself: only its deadline or a stop ends it
            self.spawn.never.wait(timeout)
            return None
        if kind == "garbage":
            return 2, "", "pubdeploy: no\n"
        if kind == "other":
            return self.printed("ready", need="k8s.secret:other")
        return self.printed(kind, *rest)

    def terminate(self) -> None:
        self.terminated = True

    def budget(self) -> None:
        self.budgeted = True


class Spawn:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.outcomes: dict[tuple[str, bool], tuple] = {}
        self.states: dict[str, str] = {}
        self.gates: dict[str, threading.Event] = {}
        self.barrier = threading.Barrier(2)
        self.never = threading.Event()

    def __call__(self, argv: list[str], cwd: Path, env: dict[str, str]) -> Proc:
        self.calls.append(argv)
        need, fulfil = argv[argv.index("--need") + 1], "--fulfil" in argv
        assert "--budget-seconds" in argv
        default = (self.states.get(need, "ready"),) if not fulfil else ("ready",)
        return Proc(self, need, fulfil, self.outcomes.get((need, fulfil), default))


def machine(needs: dict[str, dict]) -> dict:
    """The unit's machine, as pubdeploy compiles it: a region per need, keyed as pubdeploy keys it."""
    plan = [{"id": name, **{k: v for k, v in meta.items() if k != "mode"}} for name, meta in needs.items()]
    return need_machine.compile(plan, modes={name: meta["mode"] for name, meta in needs.items()})


class Live:
    def __init__(self, world: World) -> None:
        self.world, self.spawn = world, Spawn()
        self.needs: dict[str, dict] = {}
        self.run = None
        self.answer = None
        self.first_key = ""

    def runs_for(self, storage: Storage) -> Runs:
        w = self.world
        return Runs(w.approvals, Ledger(storage, head=HEAD, name="run.json"),
                    lambda runs: Executor(runs, spawn=self.spawn, lock_seconds=0.3, slice_=0.01),
                    image="registry.example/engine:1", poll_ms=2000, log=w.lines.append, clock=lambda: w.now)

    def compile(self) -> None:
        self.world.runner.machines[REPOSITORY] = Ran(0, json.dumps(machine(self.needs)), "")


@pytest.fixture
def live(ctx, tmp_path) -> Live:
    found = Live(World(tmp_path))
    found.ctx = ctx
    return found


def _bind(live: Live, runs: Runs | None) -> None:
    w = live.world
    live.runs = runs
    w.client = TestClient(create_app(w.engine, Journals(w.storage), w.approvals, w.callers, site=SITE,
                                     key=b"k" * 32, clock=lambda: w.now, runs=runs))


@given(parsers.parse("the live needs {found}"))
def live_needs(live: Live, found: str) -> None:
    w = live.world
    parts = re.findall(r'"([^"]+)" (\w+)(?: graded (\w+))?(?: stop (\w+))?(?: after "([^"]+)")?', found)
    states = {n: s for n, s, *_ in parts}
    w.runner.walk_prints(REPOSITORY, journal(states=states, grades={n: g for n, _, g, _, _ in parts if g}), code=1)
    w.engine.round()
    for name, state, grade, stop, after in parts:
        live.needs[name] = {"mode": "fulfil", "grade": grade or "auto", "type": name.split(":")[0],
                            "stop": stop or "signal", "after": [after] if after else [], "seconds": 30,
                            **({"stop_warning": WARNING} if stop == "continue" else {})}
        live.spawn.states[name] = {"missing": "missing", "drift": "drift"}.get(state, state)
    live.compile()
    _bind(live, live.runs_for(w.storage))


@given("each need's fulfil waits until the other's has started")
def barrier(live: Live) -> None:
    for name in live.needs:
        live.spawn.outcomes[(name, True)] = ("barrier",)


@given(parsers.parse('the fulfil of "{need}" waits until it is let go'))
def gated(live: Live, need: str) -> None:
    live.spawn.gates[need] = threading.Event()
    live.spawn.outcomes[(need, True)] = ("gate",)


@given("every fulfil waits until it is let go")
def all_gated(live: Live) -> None:
    for name in live.needs:
        gated(live, name)


@given(parsers.parse('the fulfil of "{need}" leaves it missing'))
def leaves_missing(live: Live, need: str) -> None:
    live.spawn.outcomes[(need, True)] = ("missing",)


@given(parsers.parse('the check of "{need}" finds it ready, saying "{detail}"'))
def ready_saying(live: Live, need: str, detail: str) -> None:
    live.spawn.outcomes[(need, False)] = ("ready", detail)


@given(parsers.parse('the check of "{need}" prints no journal'))
def no_journal(live: Live, need: str) -> None:
    live.spawn.outcomes[(need, False)] = ("garbage",)


@given(parsers.parse('the check of "{need}" names another need'))
def other_need(live: Live, need: str) -> None:
    live.spawn.outcomes[(need, False)] = ("other",)


@given(parsers.parse('the check of "{need}" finds it unknown'))
def unknown(live: Live, need: str) -> None:
    live.spawn.outcomes[(need, False)] = ("unknown",)


@given(parsers.parse('the fulfil of "{need}" runs past its budget'))
def past_budget(live: Live, need: str) -> None:
    live.needs[need]["seconds"] = 0.05
    live.compile()
    live.spawn.outcomes[(need, True)] = ("hang",)
    live.runs.executor.grace = 0.0


@given(parsers.parse('the walk of "{need}" cannot be made'))
def cannot_make(live: Live, need: str, monkeypatch) -> None:
    def broken(*a, **k):
        raise RuntimeError("no clone")
    monkeypatch.setattr(live.world.engine, "need_command", broken)


@when("a walk of Sustenaut/notifier starts and keeps running")
def walk_starts(live: Live) -> None:
    live.world.engine.walking(REPOSITORY).acquire()


@given("a walk of Sustenaut/notifier keeps running")
def walk_running(live: Live) -> None:
    live.world.engine.walking(REPOSITORY).acquire()
    live.world.approvals.lock_seconds = 0.2


@given("pubdeploy compiles no machine")
def no_machine(live: Live) -> None:
    live.world.runner.machines.pop(REPOSITORY)


@given("storage refuses every write once the run awaits approval")
def refuse_later(live: Live) -> None:
    storage = live.world.storage
    upload = storage.upload_file

    def refusing(data: bytes, *, name: str) -> dict:
        if name == "run.json" and b'"to": "awaiting"' in data:
            storage.refuse_writes = True
        return upload(data, name=name)
    storage.upload_file = refusing


# --- Asking

def _ask(live: Live, body: dict, token: str | None = None):
    w = live.world
    live.answer = answered(live.ctx, w.client.post("/requests", json=body,
                                                   headers={"Authorization": f"Bearer {token or w.token()}"}))
    if live.answer.status_code == 201 and live.runs is not None:
        live.run = live.runs.runs[live.answer.json()["id"]]
    return live.answer


@when(parsers.re(r"a GitHub run asks for a live (?P<kind>fulfil|check)(?P<modes>, \"[^\"]+\" to check)?$"))
def asks_live(live: Live, kind: str, modes: str | None) -> None:
    body = {"kind": kind, "unit": "notifier", "mode": "live"}
    if modes:
        body["modes"] = {re.search(r'"([^"]+)"', modes)[1]: "check"}
    assert _ask(live, body).status_code == 201, live.answer.text
    live.world.approvals.wait()
    live.first_key = live.run.plan_key


@when("a GitHub run asks for a live fulfil, which is still planning")
def asks_planning(live: Live) -> None:
    lock = live.world.engine.walking(REPOSITORY)
    lock.acquire()
    live.world.approvals.lock_seconds = 5
    assert _ask(live, {"kind": "fulfil", "unit": "notifier", "mode": "live"}).status_code == 201
    live.release = lock


@when("the person approves the run")
def approves(live: Live) -> None:
    live.world.approvals.wait()
    answer = live.world.client.post(f"/runs/{live.run.id}/approve", json={"plan_key": live.run.plan_key},
                                    headers=person())
    assert answer.status_code == 200, answer.text


@when("the person stops the run")
def stops(live: Live) -> None:
    answer = live.world.client.post(f"/runs/{live.run.id}/stop", headers=person())
    assert answer.status_code == 200, answer.text


def _until(live: Live, done) -> None:
    """Wait, on the runs' own condition (every move notifies it), until `done` holds."""
    with live.runs.changed:
        assert live.runs.changed.wait_for(done, timeout=10), {n: x.state for n, x in live.run.needs.items()}


@when(parsers.parse("the needs {names} are fulfilling"))
def are_fulfilling(live: Live, names: str) -> None:
    wanted = re.findall(r'"([^"]+)"', names)

    def walking() -> bool:  # fulfilling, and its fulfil's walk begun (spawned under the runs' lock)
        begun = {c[c.index("--need") + 1] for c in live.spawn.calls if "--fulfil" in c}
        return all(live.run.needs[n].state == "fulfilling" and n in begun for n in wanted)
    _until(live, walking)


@when(parsers.parse('the fulfil of "{need}" is let go'))
def let_go(live: Live, need: str) -> None:
    live.spawn.gates[need].set()


@when(parsers.parse('the person cancels "{need}" typing "{typed}"'))
def cancels(live: Live, need: str, typed: str) -> None:
    answer = live.world.client.post(f"/runs/{live.run.id}/needs/{need}/cancel", json={"confirm": typed},
                                    headers=person())
    assert answer.status_code == 200, answer.text
    _settle(live, need)


@when(parsers.parse('the person switches "{need}" to "{mode}"'))
def switches(live: Live, need: str, mode: str) -> None:
    answer = live.world.client.post(f"/runs/{live.run.id}/needs/{need}/mode", json={"mode": mode}, headers=person())
    assert answer.status_code == 200, answer.text


def _event(live: Live, body, token: str | None = "run"):
    headers = {"Authorization": f"Bearer {live.run.token if token == 'run' else token}"} if token else {}
    return live.world.client.post(f"/runs/{live.run.id}/events", json=body, headers=headers)


@when(parsers.parse('the run\'s executor posts the event "{need}" from "{frm}" to "{to}"'))
def posts_event(live: Live, need: str, frm: str, to: str) -> None:
    live.event_body = {"seq": live.run.needs[need].seq + 1, "need": need, "from": frm, "to": to, "at": "now"}
    live.answers = [_event(live, live.event_body)]


@when("the run's executor posts the same event again")
def posts_again(live: Live) -> None:
    live.answers.append(_event(live, live.event_body))


@when("a second GitHub run asks for a live fulfil that awaits approval")
def second_run(live: Live) -> None:
    live.walking = live.run
    w = live.world
    body = {"kind": "fulfil", "unit": "notifier", "mode": "live"}
    answer = w.client.post("/requests", json=body, headers={"Authorization": f"Bearer {w.token(run_id='778')}"})
    assert answer.status_code == 201, answer.text
    live.awaiting = live.runs.runs[answer.json()["id"]]
    w.approvals.wait()
    assert live.awaiting.state == "awaiting"


@when(parsers.parse('storage\'s record of the run says "{need}" went from "{frm}" to "{to}"'))
def record_rewritten(live: Live, need: str, frm: str, to: str) -> None:
    document = live.runs.ledger.load()
    for record in [*document["ended"], *document["open"]]:
        if record["id"] == live.run.id:
            kept = [e for e in record["journal"] if e.get("need") != need]
            record["journal"] = [*kept, {"seq": len(kept) + 1, "need": need, "need_seq": 1, "from": frm, "to": to,
                                         "at": kept[-1]["at"], "by": "executor"}]
    live.runs.ledger.append(document)


@then(parsers.parse('"{need}" failed by its machine\'s event "{event}", not ok, saying "{said}"'))
def failed_not_ok(live: Live, need: str, event: str, said: str) -> None:
    (last,) = [e for e in live.run.journal if e.get("need") == need and e["to"] == "failed"]
    assert last["from"] == "fulfilling" and last["event"] == event and last["ok"] is False, last
    assert said in last["result"], last


@then(parsers.parse('the new pod\'s request ended "{state}", saying "{said}"'))
def new_pod_request(live: Live, state: str, said: str) -> None:
    ask = live.world.approvals.asks[live.run.id]
    assert ask.state == state and said in ask.error, (ask.state, ask.error)


@then(parsers.parse('approving its request now is refused, the request being "{state}"'))
def approve_request_now(live: Live, state: str) -> None:
    refused = live.world.approvals.decide(live.run.id, WHO, True)  # its run is not in this pod's runs
    assert f"that request is {state}" in refused, refused


@then(parsers.parse("approving the run now answers {status:d}"))
def approve_now(live: Live, status: int) -> None:
    _bind(live, live.runs)  # the new pod's page
    answer = live.world.client.post(f"/runs/{live.run.id}/approve", json={"plan_key": live.run.plan_key},
                                    headers=person())
    assert answer.status_code == status, answer.text


@then("the new pod leaves the run out, and its log says so")
def left_out(live: Live) -> None:
    assert live.run.id not in live.runs.runs
    assert any(live.run.id in line and "left out" in line for line in live.world.lines), live.world.lines


@when("a new pod starts the runs from storage")
def new_pod(live: Live) -> None:
    w = live.world
    w.approvals = w.new_approvals()
    live.old = live.runs
    live.runs = live.runs_for(w.storage)
    w.approvals.start()
    live.runs.start()


@when("the person starts a live fulfil of the unit on the page")
def page_start(live: Live) -> None:
    live.answer = answered(live.ctx, live.world.client.post(
        "/runs", json={"repository": REPOSITORY, "unit": "notifier", "mode": "fulfil"}, headers=person()))
    live.world.approvals.wait()
    live.run = live.runs.runs[live.answer.json()["id"]]


@when("the person approves it with its plan key")
def approves_key(live: Live) -> None:
    assert live.world.client.post(f"/runs/{live.run.id}/approve", json={"plan_key": live.run.plan_key},
                                  headers=person()).status_code == 200


@when(parsers.re(r"(?P<asker>a GitHub run|the person) asks (?P<how>for a fulfil in mode .*|for a fulfil with modes .*|"
                 r"for an inline fulfil with modes|for a live fulfil of an engine with no runs|to start a run .*|"
                 r"for the run .*)"))
def asks_wrong(live: Live, asker: str, how: str) -> None:
    w = live.world
    if how == "for a fulfil in mode \"fast\"":
        _ask(live, {"kind": "fulfil", "unit": "notifier", "mode": "fast"})
    elif how == "for a fulfil with modes \"nope\"":
        _ask(live, {"kind": "fulfil", "unit": "notifier", "mode": "live", "modes": "nope"})
    elif how == "for an inline fulfil with modes":
        _ask(live, {"kind": "fulfil", "unit": "notifier", "modes": {"k8s.secret:a": "check"}})
    elif how == "for a live fulfil of an engine with no runs":
        w.approvals.runs = None
        _bind(live, None)
        _ask(live, {"kind": "fulfil", "unit": "notifier", "mode": "live"})
    elif how == "to start a run of mode \"fast\"":
        live.answer = answered(live.ctx, w.client.post("/runs", json={"mode": "fast"}, headers=person()))
    elif how == "to start a run with a body that is not JSON":
        live.answer = answered(live.ctx, w.client.post("/runs", content=b"{", headers=person()))
    elif how == "to start a run of a unit not there":
        live.answer = answered(live.ctx, w.client.post(
            "/runs", json={"repository": REPOSITORY, "unit": "x", "mode": "fulfil"}, headers=person()))
    elif how == "to start a run on an engine with no runs":
        _bind(live, None)
        live.answer = answered(live.ctx, w.client.post("/runs", json={"mode": "fulfil"}, headers=person()))
    else:
        live.answer = answered(live.ctx, w.client.get("/runs/nope", headers=person()))


@when("25 hours pass")
def hours_pass(live: Live) -> None:
    live.world.now += 25 * 3600
    live.world.client.get("/approvals", headers=person())


# --- What came of it

def _settle(live: Live, need: str | None = None) -> None:
    _until(live, lambda: live.run.needs[need].ended if need else live.run.ended)


def _ended(live: Live) -> None:
    live.world.approvals.wait()
    _settle(live)
    live.runs.executor.wait()


@then(parsers.re(r'the run ends "(?P<state>\w+)"(?: with (?P<found>.*))?$'))
def run_ends(live: Live, state: str, found: str | None) -> None:
    _ended(live)
    assert live.run.state == state, (live.run.state, {n: (x.state, x.result) for n, x in live.run.needs.items()})
    for name, need_state in re.findall(r'"([^"]+)" (\w+)', found or ""):
        assert live.run.needs[name].state == need_state, (name, live.run.needs[name])


@then(parsers.parse('the run ends "{state}", and nothing was fulfilled'))
def ends_unfulfilled(live: Live, state: str) -> None:
    run_ends(live, state, None)
    assert not [c for c in live.spawn.calls if "--fulfil" in c]


@then("nothing was fulfilled")
def nothing_fulfilled(live: Live) -> None:
    assert not [c for c in live.spawn.calls if "--fulfil" in c]


@then(parsers.re(r'the request ended "(?P<state>\w+)"(?:, saying "(?P<said>[^"]*)")?$'))
def request_ended(live: Live, state: str, said: str | None) -> None:
    _ended(live)
    ask = live.world.approvals.asks[live.run.request_id]
    assert ask.state == state, (ask.state, ask.error, ask.note)
    if said:
        assert said in ask.error + ask.note, (ask.error, ask.note)


@then(parsers.parse('the run is "{state}", with the machine pubdeploy compiled for "{mode}" and the needs {names} waiting'))
def run_is(live: Live, state: str, mode: str, names: str) -> None:
    run = live.run
    assert run.state == state and run.machine["states"]["running"]["type"] == "parallel"
    (call,) = [argv for argv, _, _ in live.world.runner.calls if argv[:2] == ["./pubdeploy", "machine"]]
    assert call == ["./pubdeploy", "machine", "--unit", "notifier", "--mode", mode]
    assert {n: x.state for n, x in run.needs.items()} == {n: "waiting" for n in re.findall(r'"([^"]+)"', names)}
    view = live.world.client.get(f"/runs/{run.id}", headers={**person(), "Accept": "application/json"}).json()
    assert view["request_id"] == run.id and view["image"] == "registry.example/engine:1"
    assert view["github_run_url"].endswith("/actions/runs/777") and view["plan_key"] == run.plan_key


@then("the request's answer links the run")
def links_run(live: Live) -> None:
    answer = live.world.client.get(f"/requests/{live.run.id}",
                                   headers={"Authorization": f"Bearer {live.world.token()}"}).json()
    assert answer["mode"] == "live" and answer["run"] == f"{SITE}/runs/{live.run.id}"


@then("the run's journal is in storage's head convergence.runs")
def in_storage(live: Live) -> None:
    events = live.world.storage.events(HEAD)
    assert [e["event"]["to"] for e in events] == ["planning", "awaiting"]
    assert all("token" not in json.dumps(e) for e in events) and live.run.token not in json.dumps(events)


@then("each need was walked to check, then to fulfil as the person")
def walked_twice(live: Live) -> None:
    for name in live.needs:
        mine = [c for c in live.spawn.calls if c[c.index("--need") + 1] == name]
        assert ["--fulfil" in c for c in mine] == [False, True], mine


@then(parsers.parse('"{need}" is "{state}" and "{other}" is "{other_state}"'))
def two_states(live: Live, need: str, state: str, other: str, other_state: str) -> None:
    are_fulfilling(live, f'"{need}"') if state == "fulfilling" else None
    assert live.run.needs[need].state == state and live.run.needs[other].state == other_state


@then(parsers.parse('"{later}" started after "{earlier}" was ready'))
def started_after(live: Live, later: str, earlier: str) -> None:
    seqs = {(e["need"], e["to"]): e["seq"] for e in live.run.journal}
    assert seqs[(later, "checking")] > seqs[(earlier, "ready")]


@then(parsers.parse('"{need}" is "{state}", saying "{said}"'))
def is_saying(live: Live, need: str, state: str, said: str) -> None:
    _settle(live, need)
    assert live.run.needs[need].state == state and said in live.run.needs[need].result


@then(parsers.parse('"{need}" is still "{state}", and the journal says "{said}"'))
def still(live: Live, need: str, state: str, said: str) -> None:
    assert live.run.needs[need].state == state
    journal_says(live, said)


@then(parsers.parse('the journal says "{said}"'))
def journal_says(live: Live, said: str) -> None:
    assert any(said in (e.get("result") or "") for e in live.run.journal), live.run.journal


@then(parsers.parse('cancelling "{need}" typing "{typed}" is refused with {status:d}'))
def cancel_refused(live: Live, need: str, typed: str, status: int) -> None:
    answer = live.world.client.post(f"/runs/{live.run.id}/needs/{need}/cancel", json={"confirm": typed},
                                    headers=person())
    assert answer.status_code == status, answer.text


@then("the run's plan key changed, and the request's binds to it")
def key_changed(live: Live) -> None:
    assert live.run.plan_key != live.first_key
    assert live.world.approvals.asks[live.run.request_id].plan_sha256 == live.run.plan_key


@then(parsers.parse('switching "{need}" to "{mode}" is refused with {status:d}'))
def switch_refused(live: Live, need: str, mode: str, status: int) -> None:
    answer = live.world.client.post(f"/runs/{live.run.id}/needs/{need}/mode", json={"mode": mode}, headers=person())
    assert answer.status_code == status, answer.text


@then(parsers.parse('"{need}" was never fulfilled'))
def never_fulfilled(live: Live, need: str) -> None:
    assert not [c for c in live.spawn.calls if "--fulfil" in c and need in c]


@then("both answers are the same event, and the journal holds it once")
def same_event(live: Live) -> None:
    first, second = (a.json() for a in live.answers)
    assert all(a.status_code == 200 for a in live.answers) and first == second
    assert [e["seq"] for e in live.run.journal].count(first["seq"]) == 1


@then(parsers.parse('an event from "{frm}" to "{to}" now is refused with {status:d}'))
def event_refused(live: Live, frm: str, to: str, status: int) -> None:
    body = {"seq": live.run.needs["k8s.secret:a"].seq + 1, "need": "k8s.secret:a", "from": frm, "to": to, "at": "now"}
    assert _event(live, body).status_code == status


@then(parsers.parse("an event out of its need's order is refused with {status:d}"))
def event_out_of_order(live: Live, status: int) -> None:
    need = live.run.needs["k8s.secret:a"]
    body = {"seq": need.seq + 2, "need": need.name, "from": need.state, "to": "ready", "at": "now"}
    assert _event(live, body).status_code == status


@then("the event carries the need's own sequence number")
def need_seq(live: Live) -> None:
    event = live.answers[0].json()
    assert event["need_seq"] == 1 and event["seq"] > 1


@then(parsers.parse("an event without the run's token is refused with {status:d}"))
def event_no_token(live: Live, status: int) -> None:
    assert _event(live, live.event_body, token=None).status_code == status
    assert _event(live, live.event_body, token="another").status_code == status


@then(parsers.parse("an event that names no need is refused with {status:d}"))
def event_no_need(live: Live, status: int) -> None:
    assert _event(live, {"seq": 1}).status_code == status
    assert live.world.client.post(f"/runs/{live.run.id}/events", content=b"{",
                                  headers={"Authorization": f"Bearer {live.run.token}"}).status_code == 400


@then(parsers.parse('the walking run ended "{state}" with "{need}" failed, saying "{said}"'))
def walking_ended(live: Live, state: str, need: str, said: str) -> None:
    run = live.runs.runs[live.walking.id]
    assert run.state == state and run.needs[need].state == "failed" and said in run.needs[need].result
    assert live.world.approvals.asks[run.id].state == "failed"
    live.spawn.gates[need].set()
    live.old.executor.wait()


@then("the run awaiting approval still awaits it in the new pod")
def still_awaiting(live: Live) -> None:
    run = live.runs.runs[live.awaiting.id]
    assert run.state == "awaiting" and {n.state for n in run.needs.values()} == {"waiting"}
    assert run.machine == live.awaiting.machine and run.plan_key == live.awaiting.plan_key


@then(parsers.parse('the run\'s page answer shows "{shown}" and holds no email or link'))
def page_public(live: Live, shown: str) -> None:
    text = live.world.client.get(f"/runs/{live.run.id}", headers=person()).text
    assert shown in text and "@" not in text and "https://example.org" not in text


@then(parsers.parse("the run's answer may be kept an hour, and asks for a poll every {ms:d} ms"))
def kept(live: Live, ms: int) -> None:
    answer = live.world.client.get(f"/runs/{live.run.id}?since=2", headers=person())
    assert answer.headers["Cache-Control"] == "private, max-age=3600" and answer.json()["poll_ms"] == ms
    assert all(e["seq"] > 2 for e in answer.json()["journal"])


@then(parsers.parse("a stop of the ended run answers {status:d}"))
def stop_ended(live: Live, status: int) -> None:
    assert live.world.client.post(f"/runs/{live.run.id}/stop", headers=person()).status_code == status
    assert live.runs.stop(live.run, WHO) == "ended"


@then(parsers.parse('"{need}" says "{said}"'))
def need_says(live: Live, need: str, said: str) -> None:
    assert said in live.run.needs[need].result, live.run.needs[need].result


@then("the answer is 201, a run \"awaiting\" asked by the person")
def started_by_person(live: Live) -> None:
    assert live.answer.status_code == 201 and live.answer.headers["location"] == f"/runs/{live.run.id}"
    assert live.run.state == "awaiting" and live.world.approvals.asks[live.run.id].requester.name == WHO


@then(parsers.parse("approving it with another plan key is refused with {status:d}"))
def wrong_key(live: Live, status: int) -> None:
    assert live.world.client.post(f"/runs/{live.run.id}/approve", json={"plan_key": "0"},
                                  headers=person()).status_code == status


@then(parsers.parse("approving it again is refused with {status:d}"))
def approve_again(live: Live, status: int) -> None:
    assert live.world.client.post(f"/runs/{live.run.id}/approve", json={"plan_key": live.run.plan_key},
                                  headers=person()).status_code == status


@then(parsers.parse("stopping it is refused with {status:d}"))
def stop_planning(live: Live, status: int) -> None:
    assert live.world.client.post(f"/runs/{live.run.id}/stop", headers=person()).status_code == status
    live.release.release()
    live.world.approvals.wait()


@then(parsers.parse('the run is "{state}", and the log says the journal refused an event'))
def refused_event(live: Live, state: str) -> None:
    assert live.run.state == state
    assert any("the journal refused event" in line for line in live.world.lines)


@then(parsers.parse('the journal says "{said}", by the person'))
def journal_says_by(live: Live, said: str) -> None:
    assert any(said in (e.get("result") or "") and e["by"] == WHO for e in live.run.journal), live.run.journal
    view = live.world.client.get(f"/runs/{live.run.id}", headers=person()).text
    assert WHO not in view


@given("the service's pubdeploy has no machine command")
def machine_too_old(live: Live) -> None:
    live.world.runner.machines[REPOSITORY] = Ran(
        2, "", "pubdeploy: error: argument command: invalid choice: 'machine' (choose from 'walk', 'check')\n")


@given("pubdeploy compiles a machine it does not run")
def machine_unrunnable(live: Live) -> None:
    bad = machine(live.needs)
    for region in bad["states"]["running"]["states"].values():
        region["meta"]["mode"] = "sometimes"
    live.world.runner.machines[REPOSITORY] = Ran(0, json.dumps(bad), "")


@given("pubdeploy compiles a machine that is not JSON")
def machine_garbage(live: Live) -> None:
    live.world.runner.machines[REPOSITORY] = Ran(0, "<html>", "")


@given("pubdeploy machine cannot run")
def machine_raises(live: Live, monkeypatch) -> None:
    def broken(*a, **k):
        raise OSError("no clone")
    monkeypatch.setattr(live.world.engine, "machine", broken)


def _refuse_move(live: Live, monkeypatch, state: str) -> None:
    from convergence.approvals import JournalRefused  # noqa: PLC0415
    approvals = live.world.approvals
    move = approvals._move  # noqa: SLF001

    def refusing(ask, to, by, **changed):
        if to == state:
            raise JournalRefused("RuntimeError")
        return move(ask, to, by, **changed)
    monkeypatch.setattr(approvals, "_move", refusing)


@given("the journal refuses the request's awaiting")
def refuse_awaiting(live: Live, monkeypatch) -> None:
    _refuse_move(live, monkeypatch, "awaiting")


@given("the journal refuses the request's start")
def refuse_start(live: Live, monkeypatch) -> None:
    _refuse_move(live, monkeypatch, "running")


@given("the engine has a Bitwarden read token")
def bitwarden(live: Live, tmp_path) -> None:
    import dataclasses  # noqa: PLC0415
    (tmp_path / "bws").write_text("bws-read")
    engine = live.world.engine
    engine.settings = dataclasses.replace(engine.settings, bitwarden_token=tmp_path / "bws")


@then("each walk ran through ./pubsec run")
def through_pubsec(live: Live) -> None:
    assert live.spawn.calls and all(c[:3] == ["./pubsec", "run", "--"] for c in live.spawn.calls)


@then("stopping a need that is not walking does nothing")
def stop_idle(live: Live) -> None:
    need = live.run.needs["k8s.secret:a"]
    live.runs.executor.stop(live.run, need)
    assert need.state == "waiting"


@then(parsers.parse("a stop the request refuses answers {status:d}"))
def stop_refused(live: Live, status: int, monkeypatch) -> None:
    monkeypatch.setattr(live.world.approvals, "decide", lambda *a: "that request is not awaiting a decision")
    assert live.world.client.post(f"/runs/{live.run.id}/stop", headers=person()).status_code == status


@then("a new pod reads the ended run back as it was, each need fulfilled")
def read_back(live: Live) -> None:
    w = live.world
    w.approvals = w.new_approvals()
    again = live.runs_for(w.storage)
    w.approvals.start()
    again.start()
    run = again.runs[live.run.id]
    assert run.state == "done" and {n: (x.state, x.fulfilled, x.seq) for n, x in run.needs.items()} == \
        {n: (x.state, True, x.seq) for n, x in live.run.needs.items()}


@then(parsers.parse('a walk asked of "{need}" once the run is stopping never starts'))
def never_starts(live: Live, need: str) -> None:
    calls = len(live.spawn.calls)
    walked = live.runs.executor._walk(live.run, live.run.needs[need], False)  # noqa: SLF001 - the race, made certain
    assert walked == ("stopped", "stopped before its walk began") and len(live.spawn.calls) == calls


# A real nested launch: uvx, the exec `pubsec run` does, uvx again, then the walk's
# Python, as ./pubsec run -- ./pubdeploy runs one. uvx runs its tool as a child and
# passes SIGUSR1 on, so a shorten reaches pubdeploy's handler without a process group.
PROBE = """import os, signal, sys, time
signal.signal(signal.SIGUSR1, lambda *_: (print("SIGUSR1 handled", flush=True), sys.exit(0)))
open("ready", "w").close()
time.sleep(30)
print("no SIGUSR1", flush=True)
"""


def _probe_wheel(where: Path) -> Path:
    """A wheel with nothing in it but its name, which uvx installs offline."""
    import base64
    import hashlib
    import zipfile
    name, wheel = "sigprobe-0.0.0", where / "sigprobe-0.0.0-py3-none-any.whl"
    files = {"sigprobe/__init__.py": "", f"{name}.dist-info/METADATA": "Metadata-Version: 2.1\nName: sigprobe\nVersion: 0.0.0\n",
             f"{name}.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    record = []
    with zipfile.ZipFile(wheel, "w") as z:
        for path, text in files.items():
            z.writestr(path, text)
            digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
            record.append(f"{path},sha256={digest},{len(text)}")
        z.writestr(f"{name}.dist-info/RECORD", "\n".join([*record, f"{name}.dist-info/RECORD,,"]) + "\n")
    return wheel


@given("a walk launched as ./pubsec run -- ./pubdeploy launches one: uvx, an exec, uvx again, then python")
def nested_walk(live: Live, tmp_path: Path) -> None:
    path = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    if shutil.which("uvx", path=path) is None:  # tests/requirements.txt installs it with the suite
        pytest.skip("uvx is not on this machine")
    wheel = _probe_wheel(tmp_path)
    (tmp_path / "probe.py").write_text(PROBE)
    (tmp_path / "pubdeploy").write_text(f'#!/usr/bin/env bash\nexec uvx --from {wheel} python probe.py "$@"\n')
    (tmp_path / "pubsec").write_text(
        f"#!/usr/bin/env bash\nexec uvx --from {wheel} python -c "
        "'import os, sys; c = sys.argv[sys.argv.index(\"--\") + 1:]; os.execvp(c[0], c)' \"$@\"\n")
    for launcher in ("pubdeploy", "pubsec"):
        (tmp_path / launcher).chmod(0o755)
    env = {**os.environ, "PATH": path, "UV_CACHE_DIR": str(tmp_path / "cache"), "UV_OFFLINE": "1"}
    live.walk_dir = tmp_path
    live.proc = Popen(["./pubsec", "run", "--", "./pubdeploy", "walk", "--need", "k8s.secret:a"], tmp_path, env)


@when("the executor's walk process is told its budget has ended")
def budget_ends(live: Live) -> None:
    deadline = time.monotonic() + 60
    while not (live.walk_dir / "ready").exists():
        assert time.monotonic() < deadline and live.proc.process.poll() is None, "the walk never started"
        time.sleep(0.05)
    live.proc.budget()
    live.ended = live.proc.poll(30)


@then("the walk's own SIGUSR1 handler ran")
def handler_ran(live: Live) -> None:
    assert live.ended is not None, "the walk kept running"
    code, out, err = live.ended
    assert "SIGUSR1 handled" in out and code == 0, (code, out, err)


# --- The run's page (runpage.feature) -----------------------------------------------------------


@when("a person opens the run's page")
def opens_page(live: Live) -> None:
    live.world.approvals.wait()
    live.answer = live.world.client.get(f"/runs/{live.run.id}", headers={**person(), "Accept": "text/html"})


@then("the page is HTML that refreshes every 5 s for a reader without script")
def html_refresh(live: Live) -> None:
    assert live.answer.status_code == 200 and live.answer.headers["content-type"].startswith("text/html")
    assert '<noscript><meta http-equiv="refresh" content="5"></noscript>' in live.answer.text


@then(parsers.parse('the page shows the run\'s state "{state}" and the need "{need}"'))
def page_shows(live: Live, state: str, need: str) -> None:
    body = live.answer.text.split('<div id="page">', 1)[1]
    assert f"<b>{state}</b>" in body and need in body


def _data_block(text: str) -> dict:
    found = re.search(r'<script type="application/json" id="page-data">(.*?)</script>', text, re.S)
    assert found, text[:400]
    return json.loads(found.group(1))


@then("the page's data block holds the run's public view, its plan_key among it")
def data_block(live: Live) -> None:
    data = _data_block(live.answer.text)
    assert data["kind"] == "run" and data["may_act"] is True
    assert data["run"]["id"] == live.run.id and data["run"]["plan_key"] == live.run.plan_key
    assert "@" not in json.dumps(data) and "token" not in data["run"]


@then("the page loads /static/run.js and /static/run.css")
def loads_bundle(live: Live) -> None:
    assert '<script src="/static/run.js"></script>' in live.answer.text
    assert '<link rel="stylesheet" href="/static/run.css">' in live.answer.text


@then(parsers.parse('its Content-Security-Policy is exactly "{csp}"'))
def csp_exact(live: Live, csp: str) -> None:
    assert live.answer.headers["Content-Security-Policy"] == csp


@then(parsers.parse('the engine\'s other pages keep their Content-Security-Policy "{csp}"'))
def csp_others(live: Live, csp: str) -> None:
    assert live.world.client.get("/", headers=person()).headers["Content-Security-Policy"] == csp


@when(parsers.parse('a person asks for "{path}"'))
def asks_for(live: Live, path: str) -> None:
    _bind(live, live.runs_for(live.world.storage)) if not hasattr(live.world, "client") else None
    live.answer = live.world.client.get(path, headers=person())


@then(parsers.parse('it answers {status:d} as "{kind}", not to be sniffed'))
def answers_as(live: Live, status: int, kind: str) -> None:
    assert live.answer.status_code == status and live.answer.headers["content-type"] == kind
    assert live.answer.headers["X-Content-Type-Options"] == "nosniff" and live.answer.content


@then(parsers.parse("it answers {status:d}"))
def answers_status(live: Live, status: int) -> None:
    assert live.answer.status_code == status, live.answer.text


@when(parsers.parse("the bundle polls the run after event {seq:d}"))
def polls_after(live: Live, seq: int) -> None:
    live.world.approvals.wait()
    live.answer = live.world.client.get(f"/runs/{live.run.id}?since={seq}", headers=person())
    live.since = seq


@then(parsers.parse("the answer is JSON with only the events after {seq:d}"))
def json_after(live: Live, seq: int) -> None:
    body = live.answer.json()
    assert body["journal"] and all(e["seq"] > seq for e in body["journal"])
    assert len(body["journal"]) == len(live.run.journal) - seq


@when("the bundle asks for the run with Accept: application/json")
def asks_json(live: Live) -> None:
    live.answer = live.world.client.get(f"/runs/{live.run.id}", headers={**person(), "Accept": "application/json"})


@then("the answer is JSON with every event")
def json_all(live: Live) -> None:
    assert [e["seq"] for e in live.answer.json()["journal"]] == [e["seq"] for e in live.run.journal]


@when(parsers.parse('a person opens "{path}"'))
def opens_path(live: Live, path: str) -> None:
    live.answer = live.world.client.get(path, headers={**person(), "Accept": "text/html"})


@when(parsers.parse('someone not signed in opens "{path}"'))
def stranger_opens(live: Live, path: str) -> None:
    live.answer = live.world.client.get(path, headers={"Accept": "text/html"})


@then(parsers.parse('the page\'s data block lists the unit "{unit}" of the service'))
def lists_unit(live: Live, unit: str) -> None:
    data = _data_block(live.answer.text)
    assert data["kind"] == "new" and unit in data["services"][REPOSITORY], data


@when(parsers.re(r'the bundle asks for the needs of the unit "(?P<unit>[^"]+)" in mode "(?P<mode>\w+)"'
                 r"(?P<stranger>, for someone not signed in)?$"))
def asks_needs(live: Live, unit: str, mode: str, stranger: str | None) -> None:
    live.answer = live.world.client.get(f"/runs/new/needs?repository={REPOSITORY}&unit={unit}&mode={mode}",
                                        headers={} if stranger else person())


@given("the engine walks as it always does")
def as_always(live: Live) -> None:
    """Nothing changed: the scenario's other steps say what it asks."""


@given("the engine has no live runs")
def no_live_runs(live: Live) -> None:
    _bind(live, None)


@then(parsers.parse('the needs read "{listed}"'))
def needs_read(live: Live, listed: str) -> None:
    assert live.answer.status_code == 200, live.answer.text
    got = ", ".join(f"{n['name']} {n['mode']}" for n in live.answer.json()["needs"])
    assert got == listed


def _read_as_executor(live: Live, token: str):
    return live.world.client.get(f"/runs/{live.run.id}/executor", headers={"Authorization": f"Bearer {token}"})


@then("the run read with its token answers its needs as JSON")
def read_with_token(live: Live) -> None:
    answer = _read_as_executor(live, live.run.token)
    assert answer.status_code == 200 and answer.headers["content-type"].startswith("application/json")
    data = answer.json()
    assert set(data["needs"]) == set(live.run.needs) and data["cancelled"] == []
    assert live.run.token not in answer.text


@then(parsers.parse("the run read with another token is refused with {status:d}"))
def read_other_token(live: Live, status: int) -> None:
    answer = _read_as_executor(live, "another")
    assert answer.status_code == status and answer.json() == {"refused": "not-this-runs-executor"}


@then(parsers.parse('the run, read with its token, lists "{need}" as cancelled'))
def lists_cancelled(live: Live, need: str) -> None:
    assert _read_as_executor(live, live.run.token).json()["cancelled"] == [need]


@when(parsers.parse("the run's executor posts its exit {code:d}"))
def posts_exit(live: Live, code: int) -> None:
    answer = _event(live, {"executor_exit": code, "result": "lost"})
    assert answer.status_code == 200, answer.text


@then(parsers.parse('the journal says "{said}", by the executor'))
def journal_says_executor(live: Live, said: str) -> None:
    assert any(said in (e.get("result") or "") and e["by"] == "executor" for e in live.run.journal), live.run.journal


@then("the run's executor posting its exit again changes nothing")
def exit_again(live: Live) -> None:
    states = {n: x.state for n, x in live.run.needs.items()}
    assert _event(live, {"executor_exit": 0}).status_code == 200
    for gate in live.spawn.gates.values():
        gate.set()  # the engine's own executor, still walking: it finds the run ended
    live.runs.executor.wait()
    assert live.run.state == "failed" and {n: x.state for n, x in live.run.needs.items()} == states
    assert [e["to"] for e in live.run.journal].count("failed") == 2  # a's move, and the run's end, once


def _poll_by(live: Live, who: str) -> list[dict]:
    live.world.approvals.wait()
    answer = live.world.client.get(f"/runs/{live.run.id}?since=0", headers=person(who))
    return answer.json()["journal"]


@then(parsers.parse('the run\'s poll, for the person who approved it, says the approval was by "{by}"'))
def approved_by_viewer(live: Live, by: str) -> None:
    journal = _poll_by(live, WHO)
    assert any(e["to"] == "approved" and e["by"] == by for e in journal), journal


@then(parsers.parse('the run\'s poll, for another person, says the approval was by "{by}"'))
def approved_by_other(live: Live, by: str) -> None:
    journal = _poll_by(live, "other@example.org")
    assert any(e["to"] == "approved" and e["by"] == by for e in journal), journal


@then('no poll names an email or "[someone]" as who acted')
def no_email_actor(live: Live) -> None:
    for who in (WHO, "other@example.org"):
        for e in _poll_by(live, who):
            assert "@" not in e["by"] and "[someone]" not in e["by"], e
