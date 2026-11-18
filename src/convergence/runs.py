"""Live runs: a fulfil or check walked need by need, its state shown as it changes.

A request with `"mode": "live"` (POST /requests, or a signed-in approver's POST /runs)
makes a run beside it. Its plan walk is the request's (`check --unit U`), and its
machine is the service's own: `pubdeploy machine --unit U --mode M [--modes ...]`
compiles the deploy block's `run` section into XState v5 JSON, a parallel state
`running` with one child per need, each need's meta naming its mode, grade, type, stop
class, after-edges and budget. The run keeps that machine as it was asked: a later
change of the block never rewrites a run in flight.

Each need moves through waiting, checking, then ready, or found (missing or drift),
which a fulfil need of grade auto leaves for fulfilling and a check again; failed,
blocked and stopped end it too. A need starts once every need it comes after has
ended; one after a need that failed, was blocked or stopped is blocked. The executor
(executor.py) walks each need with `pubdeploy walk --need NAME`, as many at once as the
machine's `parallel` allows, and reports each move here (transition), as a remote
executor will through POST /runs/ID/events.

Each move is one event: its sequence number, need, from, to, when, by whom, and a
result that is public() (no email, no link). The run's journal is those events, and
each is written, with the run as it then is, as one event of storage's head
`convergence.runs` (tenant plane), before it counts: that head is the store a new pod
reads its runs back from. A run awaiting approval waits on; one that was walking ended
with its pod (the executor ran in it), so its needs not ended fail, "executor lost", and
nothing walks again.

The run's mode switches (each need check or fulfil) may change only before approval,
which freezes them; the plan's key, which the approval binds to, is computed with them.
Stop before approval declines; while running it stops what has not started, and each
need walking as its stop class says: signal (terminated), shorten (its budget ends now)
or continue (noted: it must finish, and only its own cancel, with its name typed, ends
it). The run's end goes to its request, which ends in the same state, and each need's
journal to the need-states facts.
"""

from __future__ import annotations

import calendar
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .approvals import Ask, JournalRefused, Ledger, nothing_to_fulfil, plan_of, plan_sha256, public

HEAD = "convergence.runs"
SCHEMA = "convergence-run/1"
FINAL = ("ready", "failed", "blocked", "stopped")
STATES = ("waiting", "checking", "found", "fulfilling", *FINAL)
# Each need state's moves; `found` ends a need unless it is fulfilled.
MOVES = {"waiting": ("checking", "blocked", "stopped"),
         "checking": ("ready", "found", "failed", "blocked", "stopped"),
         "found": ("fulfilling",),
         "fulfilling": ("checking", "failed", "stopped")}
ENDED = ("done", "failed", "declined", "expired", "stopped")
STOPS = ("signal", "shorten", "continue")
KEEP_ENDED = 50
POLL_MS = 2000


@dataclass
class Need:
    """One need of a run: its machine's meta, and where it is."""
    name: str
    mode: str = "fulfil"
    grade: str = "auto"
    type: str = ""
    stop: str = "signal"
    stop_warning: str = ""
    after: list[str] = field(default_factory=list)
    seconds: float = 60.0
    state: str = "waiting"
    since: str = ""
    result: str = ""
    fulfilled: bool = False  # a fulfil has run: a check after it that finds it again fails it

    @property
    def ended(self) -> bool:
        return self.state in FINAL or (self.state == "found" and not self.fulfils)

    @property
    def fulfils(self) -> bool:
        return self.mode == "fulfil" and self.grade == "auto" and not self.fulfilled

    def view(self) -> dict[str, Any]:
        return {"state": self.state, "mode": self.mode, "grade": self.grade, "type": self.type, "stop": self.stop,
                "stop_warning": self.stop_warning, "after": self.after, "since": self.since,
                "result": public(self.result)}


@dataclass
class Run:
    """A live request's walks, need by need."""
    id: str
    request_id: str
    repository: str
    unit: str
    mode: str  # check or fulfil
    created_at: float
    expires_at: float
    state: str = "planning"
    machine: dict[str, Any] | None = None
    plan: list[dict[str, str]] = field(default_factory=list)
    plan_key: str = ""
    needs: dict[str, Need] = field(default_factory=dict)
    modes: dict[str, str] = field(default_factory=dict)  # as asked, until the machine gives them
    journal: list[dict[str, Any]] = field(default_factory=list)
    image: str = ""
    github_run_url: str = ""
    parallel: int = 4
    stopping: str = ""  # who asked to stop it
    approver: str = ""
    token: str = ""  # a remote executor's bearer for POST /runs/ID/events: never kept or shown
    documents: list[dict[str, Any]] = field(default_factory=list)  # the needs' journals, for the facts

    @property
    def ended(self) -> bool:
        return self.state in ENDED

    def record(self) -> dict[str, Any]:
        """What storage keeps of it, and a reader sees: names, states and public() results."""
        return {"id": self.id, "request_id": self.request_id, "repository": self.repository, "unit": self.unit,
                "mode": self.mode, "state": self.state, "machine": self.machine, "plan": [
                    {**n, "change": public(n.get("change", ""))} for n in self.plan],
                "plan_key": self.plan_key, "needs": {k: n.view() for k, n in self.needs.items()},
                "journal": self.journal, "image": self.image, "created_at": _iso(self.created_at),
                "expires_at": _iso(self.expires_at), "github_run_url": self.github_run_url,
                "parallel": self.parallel, "approver": self.approver, "modes": self.modes}

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> Run:
        run = cls(data["id"], data["request_id"], data["repository"], data["unit"], data["mode"],
                  _epoch(data["created_at"]), _epoch(data["expires_at"]), state=data["state"],
                  machine=data.get("machine"), plan=data.get("plan") or [], plan_key=data.get("plan_key", ""),
                  journal=data.get("journal") or [], image=data.get("image", ""),
                  github_run_url=data.get("github_run_url", ""), parallel=data.get("parallel", 4),
                  approver=data.get("approver", ""), modes=data.get("modes") or {})
        if run.machine:
            run.needs = needs_of_machine(run.machine, run.modes)
        replay(run)
        return run


def _iso(moment: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment))


def _epoch(text: str) -> float:
    return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))


def needs_of_machine(machine: dict[str, Any], modes: dict[str, str]) -> dict[str, Need]:
    """The run's needs, from the compiled machine: one child of `running` each, its meta
    naming mode, grade, type, stop class, after-edges and budget; `modes` overrides a mode."""
    out = {}
    for name, child in ((machine.get("states") or {}).get("running") or {}).get("states", {}).items():
        meta = child.get("meta") or {}
        stop = meta.get("stop") if meta.get("stop") in STOPS else "signal"
        out[name] = Need(name, mode=modes.get(name, meta.get("mode", "fulfil")), grade=meta.get("grade", "auto"),
                         type=meta.get("type", name.split(":")[0]), stop=stop,
                         stop_warning=str(meta.get("stop_warning") or ""),
                         after=[str(a) for a in meta.get("after") or []],
                         seconds=float(meta.get("seconds") or 60))
    return out


def replay(run: Run) -> None:
    """Each need where its journal leaves it: every need starts waiting, and each event moves one."""
    for need in run.needs.values():
        need.state, need.since, need.result, need.fulfilled = "waiting", "", "", False
    for event in run.journal:
        need = run.needs.get(event.get("need") or "")
        if need is not None and event["to"] in STATES:  # a mode switch or a note moves nothing
            if need.state == "fulfilling" and event["to"] == "checking":
                need.fulfilled = True
            need.state, need.since, need.result = event["to"], event["at"], event.get("result") or ""


def run_key(plan: list[dict[str, str]], needs: dict[str, Need]) -> str:
    """The plan's key, which the approval binds to: each need's name, state, grade and mode."""
    return plan_sha256([{"need": n["need"], "state": n["state"], "grade": n["grade"],
                         "mode": needs[n["need"]].mode if n["need"] in needs else "fulfil"} for n in plan])


class Refusal(Exception):
    """A run's endpoint refusing: the status and why."""

    def __init__(self, status: int, why: str):
        super().__init__(why)
        self.status, self.why = status, why


class Runs:
    """The live runs: made with their requests, planned, approved, walked and journaled."""

    def __init__(self, approvals: Any, ledger: Ledger, executor_factory: Callable[[Runs], Any], *,
                 image: str = "", poll_ms: int = POLL_MS, log: Callable[[str], None] = print,
                 clock: Callable[[], float] = time.time):
        self.approvals, self.ledger, self.log, self.clock = approvals, ledger, log, clock
        self.engine = approvals.engine
        self.image, self.poll_ms = image, poll_ms
        self.runs: dict[str, Run] = {}
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        approvals.runs = self
        self.executor = executor_factory(self)

    # --- The record

    def _persist(self, run: Run, event: dict[str, Any]) -> None:
        ended = sorted((r for r in self.runs.values() if r.ended), key=lambda r: r.created_at)[-KEEP_ENDED:]
        self.ledger.append({"schema": SCHEMA, "run": run.id, "state": run.state, "event": event,
                            "open": [r.record() for r in self.runs.values() if not r.ended],
                            "ended": [r.record() for r in ended]})

    def _note(self, run: Run, event: dict[str, Any]) -> dict[str, Any]:
        """One event of the run: numbered, kept in its journal and written to the head. A
        refused write is said in the log; the run goes on, as its request's end does."""
        event = {"seq": len(run.journal) + 1, "at": _iso(self.clock()), **event}
        if "result" in event:
            event["result"] = public(event["result"])
        run.journal.append(event)
        try:
            self._persist(run, event)
        except JournalRefused as e:
            self.log(f"convergence: run {run.id}: the journal refused event {event['seq']} ({e})")
        self.changed.notify_all()
        return event

    def _state(self, run: Run, state: str, by: str, **said: Any) -> None:
        with self.lock:
            frm, run.state = run.state, state
            self._note(run, {"need": "", "from": frm, "to": state, "by": by, **said})

    def start(self) -> None:
        """The runs, from the head: one awaiting approval waits on; one that was walking ended
        with its pod, so its needs not ended fail, executor lost, and nothing walks again."""
        document = self.ledger.load() or {}
        for data in [*document.get("ended", []), *document.get("open", [])]:
            run = Run.from_record(data)
            run.token = secrets.token_urlsafe(24)
            self.runs[run.id] = run
        for run in [r for r in self.runs.values() if r.state in ("planning", "approved", "running")]:
            with self.lock:
                for need in run.needs.values():
                    if not need.ended:
                        self._move(run, need, "failed", "engine", "executor lost: the engine restarted")
            self._finish(run, "failed", "executor lost: the engine restarted while it walked")

    # --- Made, planned, decided

    def create(self, ask: Ask) -> Run:
        run = Run(ask.id, ask.id, ask.repository, ask.unit, ask.kind, ask.created_at, ask.expires_at,
                  image=self.image, github_run_url=ask.run_url, modes=dict(ask.modes),
                  token=secrets.token_urlsafe(24))
        with self.lock:
            self.runs[run.id] = run
            self._note(run, {"need": "", "from": "", "to": "planning", "by": ask.requester.name})
        return run

    def get(self, id_: str) -> Run | None:
        self.approvals.expire_due()
        return self.runs.get(id_)

    def planned(self, ask: Ask, journal: dict[str, Any]) -> None:
        """The plan walk's journal, and the machine: a check walks at once; a fulfil with nothing
        the fulfil changes ends as an inline one does; one with something awaits approval."""
        run = self.runs[ask.id]
        try:
            machine = self.engine.machine(run.repository, run.unit, run.mode, run.modes, ask.requester.name)
        except Exception as e:  # noqa: BLE001 - said on the run's page
            machine = f"pubdeploy machine could not run ({type(e).__name__})"
        if isinstance(machine, str):
            self._finish(run, "failed", machine)
            return
        with self.lock:
            run.machine = machine
            run.parallel = int((machine.get("meta") or {}).get("parallel") or 4)
            run.needs = needs_of_machine(machine, run.modes)
            if run.mode == "check":  # a check fulfils nothing, whatever a need's mode says
                for need in run.needs.values():
                    need.mode = "check"
            run.plan = plan_of(journal)
            run.plan_key = run_key(run.plan, run.needs)
        if run.mode == "check":  # read-only: it walks at once, and nobody is asked anything
            try:
                self.approvals._move(ask, "running", "engine", plan=run.plan, plan_sha256=run.plan_key)  # noqa: SLF001
            except JournalRefused as e:
                self._finish(run, "failed", f"the journal refused its start ({e}): nothing was walked")
                return
            self._go(run, ask.requester.name)
            return
        if not run.plan:
            self._finish(run, "done", "", needs=journal)
            return
        wanted = [n for n in run.plan if run.needs.get(n["need"], Need(n["need"])).mode == "fulfil"]
        ended = nothing_to_fulfil(wanted) if wanted else ("done", "nothing the engine can fulfil: every need is to check")
        if ended is not None:  # nothing to approve, as an inline fulfil (#18)
            self._finish(run, *ended, needs=journal)
            return
        try:
            self.approvals._move(ask, "awaiting", "engine", plan=run.plan, plan_sha256=run.plan_key)  # noqa: SLF001
        except JournalRefused as e:
            self._finish(run, "failed", f"the journal refused its plan ({e})")
            return
        self._state(run, "awaiting", "engine")

    def decided(self, ask: Ask, approve: bool, state: str = "") -> None:
        """Its request approved (the modes freeze, and it walks), declined or expired."""
        run = self.runs[ask.id]
        if not approve:
            self._state(run, state or "declined", ask.changes[-1]["by"])
            return
        run.approver = ask.approver
        self._state(run, "approved", ask.approver)
        self._go(run, ask.approver)

    def _go(self, run: Run, by: str) -> None:
        self._state(run, "running", by)
        self.executor.start(run)

    def follow(self, ask: Ask) -> None:
        """A live request that ended before its run walked (its plan walk could not start): the run too."""
        run = self.runs.get(ask.id)
        if run is not None and not run.ended:
            self._state(run, ask.state, "engine", **({"result": ask.error} if ask.error else {}))

    # --- Moves

    def _move(self, run: Run, need: Need, to: str, by: str, result: str = "") -> dict[str, Any]:
        if to not in MOVES.get(need.state, ()):
            raise Refusal(409, f"{need.name} is {need.state}: it does not move to {to}")
        if need.state == "fulfilling" and to == "checking":
            need.fulfilled = True
        frm, need.state, need.since, need.result = need.state, to, _iso(self.clock()), result
        return self._note(run, {"need": need.name, "from": frm, "to": to, "by": by, "result": result})

    def transition(self, run: Run, name: str, to: str, result: str = "", by: str = "engine") -> dict[str, Any]:
        """One need's move, as the executor reports it."""
        with self.lock:
            return self._move(run, run.needs[name], to, by, result)

    def event(self, run: Run, body: Any) -> dict[str, Any]:
        """A remote executor's event (POST /runs/ID/events): idempotent on the need and its sequence
        number, which is the run's; one that is not the next move of that need is refused."""
        if not isinstance(body, dict) or not isinstance(body.get("seq"), int) or body.get("need") not in run.needs:
            raise Refusal(400, "an event names a seq, a need of the run, from and to")
        with self.lock:
            for done in run.journal:
                if done.get("seq") == body["seq"] and done.get("need") == body["need"]:
                    return done
            need = run.needs[body["need"]]
            if body.get("from") != need.state or body["seq"] != len(run.journal) + 1:
                raise Refusal(409, f"{need.name} is {need.state} at event {len(run.journal)}")
            return self._move(run, need, str(body.get("to")), "executor", str(body.get("result") or ""))

    def set_mode(self, run: Run, name: str, mode: Any, actor: str) -> str:
        """A need's mode, switched before approval, which freezes them: the plan's key again."""
        if mode not in ("check", "fulfil"):
            raise Refusal(400, "a need's mode is check or fulfil")
        with self.lock:
            if run.state not in ("planning", "awaiting"):
                raise Refusal(409, f"the run is {run.state}: its modes froze at approval")
            if run.needs and name not in run.needs:
                raise Refusal(404, "the run has no such need")
            run.modes[name] = mode
            if name in run.needs:
                run.needs[name].mode = mode
            run.plan_key = run_key(run.plan, run.needs)
            ask = self.approvals.asks[run.request_id]
            ask.modes, ask.plan_sha256 = dict(run.modes), run.plan_key if run.plan else ask.plan_sha256
            self._note(run, {"need": name, "from": "", "to": f"mode {mode}", "by": actor})
            return run.plan_key

    # --- Stop, cancel

    def stop(self, run: Run, actor: str) -> str:
        """Stop: before approval it declines; while walking, what has not started never starts and
        each need walking stops as its class says. "" when it counted, else why it did nothing."""
        if run.ended:
            return "ended"
        if run.state in ("planning", "awaiting"):
            if run.state == "planning":
                raise Refusal(409, "the plan is being walked: stop it once it awaits approval")
            refused = self.approvals.decide(run.request_id, actor, False)
            if refused:
                raise Refusal(409, refused)
            return ""
        with self.lock:
            run.stopping = actor
            self._note(run, {"need": "", "from": run.state, "to": "stopping", "by": actor})
            for need in run.needs.values():
                if need.state in ("checking", "fulfilling"):
                    if need.stop == "continue":
                        self._note(run, {"need": need.name, "from": need.state, "to": need.state, "by": actor,
                                         "result": f"keeps running: {need.stop_warning or 'it must finish'}"})
                    else:
                        self.executor.stop(run, need)
        return ""

    def cancel(self, run: Run, name: str, body: Any, actor: str) -> None:
        """Cancel one need that must finish (stop class continue), while it fulfils: its name typed."""
        need = run.needs.get(name)
        if need is None:
            raise Refusal(404, "the run has no such need")
        if not isinstance(body, dict) or body.get("confirm") != name:
            raise Refusal(400, f"type the need's name, {name}, to cancel it")
        if need.stop != "continue" or need.state != "fulfilling":
            raise Refusal(409, f"only a need that must finish is cancelled, while it fulfils: {name} is {need.state}")
        with self.lock:
            self._note(run, {"need": name, "from": need.state, "to": need.state, "by": actor,
                             "result": f"cancelled, warned: {need.stop_warning}"})
        self.executor.stop(run, need, cancel=True)

    # --- The end

    def ended_walking(self, run: Run) -> None:
        """The executor's end: the run ends as its needs did."""
        states = {n.state for n in run.needs.values()}
        state = ("stopped" if run.stopping else "failed" if states & {"failed", "blocked", "stopped"} else "done")
        bad = ", ".join(f"{n.name} {n.state}" for n in run.needs.values() if n.state in ("failed", "blocked", "stopped"))
        self._finish(run, state, f"not ready: {bad}" if bad and state == "failed" else "")

    def _finish(self, run: Run, state: str, why: str, needs: dict[str, Any] | None = None) -> None:
        """The run's end, and its request's: the same state, each need's state, the reason."""
        self._state(run, state, "engine", **({"result": why} if why else {}))
        ask = self.approvals.asks.get(run.request_id)
        listed = [{"need": n.name, "type": n.type, "grade": n.grade, "state": n.state, "detail": public(n.result)}
                  for n in run.needs.values()]
        if needs is not None:
            from .approvals import needs_of  # noqa: PLC0415
            listed = needs_of(needs)
        for document in run.documents:
            self.engine._publish(document)  # noqa: SLF001 - the need-states facts, as a round's
        if ask is not None and not ask.ended:
            failed = state in ("failed", "stopped")
            self.approvals._end(ask, state, "engine", needs=listed, plan=run.plan,  # noqa: SLF001
                                plan_sha256=run.plan_key or ask.plan_sha256,
                                **({"error": why} if failed and why else {"note": why} if why else {}))

    # --- What a reader sees

    def view(self, run: Run, since: int = 0) -> dict[str, Any]:
        """What GET /runs/ID answers: the run, its journal after `since`, all of it public()."""
        out = run.record()
        out["approver"] = public(out["approver"])
        out["journal"] = [{**e, "by": public(e.get("by", ""))} for e in run.journal if e["seq"] > since]
        out["poll_ms"] = self.poll_ms
        return out
