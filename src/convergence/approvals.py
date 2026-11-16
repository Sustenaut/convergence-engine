"""Requests: something a caller wants done, which a person approves first.

Two kinds:

- `fulfil`: the engine walks one unit with `./pubdeploy walk --fulfil --yes --unit U`,
  as the 4b button did, once a person has approved the plan.
- `gate`: the caller polls GET /requests/ID and goes on by itself once it is approved.

A request moves forward only, one state at a time:

    planning -> awaiting | done (nothing to do) | failed      (fulfil: its plan walk)
    awaiting -> approved | declined | expired                  (CONVERGENCE_REQUEST_HOURS, 24)
    approved -> running -> done | failed                       (fulfil; a gate ends at approved)

A fulfil's plan is a read-only walk of the unit (`check --unit U`) on arrival: each
need that is not ready, by name, type, state and grade, with one line on what its
fulfil changes. Names and states only, never a value. The approval is bound to the
plan's sha256: the unit is walked read-only again just before the Fulfil, and when
that plan's hash differs, the request ends `failed`, "plan changed since approval",
and nothing is fulfilled. A unit with nothing to do ends `done` at once. A gate's
plan is the caller's text (8 KB at most), shown escaped.

Every state change appends one event to storage's tenant `plane`, head
`convergence.approvals` (the Ledger), before it counts: a change the journal refuses
does not happen, and a request whose first event is refused is not taken. Each event
holds the open requests and the last ended ones, so a new pod picks them up from the
head alone.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .callers import Refused, Requester

HEAD = "convergence.approvals"
SCHEMA = "convergence-approval/1"
KINDS = ("fulfil", "gate")
OPEN = ("planning", "awaiting", "approved", "running")
ENDED = ("declined", "expired", "done", "failed")
PLAN_BYTES = 8192
KEEP_ENDED = 50
CHANGED = "plan changed since approval"
# Why a fulfil cannot be asked for (Engine.can_fulfil's names), as said on a page.
REASONS = {"not-walked": "that service has not been walked yet",
           "no-such-unit": "that service has no such unit",
           "fulfils-nothing": "this engine fulfils nothing: it has no installation token that may write"}


class Storage(Protocol):
    def upload_file(self, data: bytes, *, name: str) -> dict: ...

    def get_head(self, name: str) -> tuple[bytes, str | None]: ...

    def move_head(self, name: str, hash: str, *, if_match: str | None = None) -> dict: ...  # noqa: A002


class JournalRefused(Exception):
    """The ledger could not append: the change does not happen."""


class Ledger:
    """The approvals' journal: one storage file per event, the head moved to each in turn,
    so the head's log is every state change, newest first."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self.last: str | None = None  # the event the head points at, which the next one follows

    def load(self) -> dict[str, Any] | None:
        try:
            data, self.last = self.storage.get_head(HEAD)
        except Exception as e:  # noqa: BLE001 - a new head has no event yet; any other refusal stops the start
            if type(e).__name__ == "NotFound":
                self.last = None
                return None
            raise
        return json.loads(data)

    def append(self, document: dict[str, Any]) -> None:
        try:
            stored = self.storage.upload_file(json.dumps({**document, "previous": self.last}, sort_keys=True).encode(),
                                              name="approval.json")
            self.storage.move_head(HEAD, stored["id"], if_match=self.last)
        except Exception as e:  # noqa: BLE001 - any refusal: the change is refused with it
            raise JournalRefused(type(e).__name__) from None
        self.last = stored["id"]


def plan_sha256(plan: Any) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _line(need: dict[str, Any]) -> str:
    """What the need's fulfil changes, in one line: names only."""
    if need.get("grade") == "auto" and need.get("state") in ("missing", "drift"):
        said = "makes it" if need["state"] == "missing" else "makes it again"
        return f"the fulfil {said}: {need.get('summary') or need['id']}".splitlines()[0][:200]
    fix = (need.get("fix") or "").strip().splitlines()
    return ("not the fulfil's: " + (fix[0] if fix else need.get("summary") or "a person's step"))[:200]


def plan_of(document: dict[str, Any]) -> list[dict[str, str]]:
    """Each need of one unit's journal that is not ready, as a request's plan shows it."""
    return [{"need": str(n["id"]), "name": str(n.get("name") or ""), "type": str(n["type"]),
             "state": str(n["state"]), "grade": str(n["grade"]), "change": _line(n)}
            for n in document["needs"] if n["state"] != "ready"]


def needs_of(document: dict[str, Any]) -> list[dict[str, str]]:
    return [{"need": str(n["id"]), "state": str(n["state"])} for n in document["needs"]]


@dataclass
class Ask:
    """One request, and everything its page shows."""
    id: str
    kind: str
    requester: Requester
    repository: str
    unit: str
    created_at: float
    expires_at: float
    state: str = "planning"
    plan: list[dict[str, str]] | None = None  # a fulfil's, once walked
    text: str = ""  # a gate's
    plan_sha256: str = ""
    changes: list[dict[str, str]] = field(default_factory=list)  # each state, when and by whom
    approver: str = ""
    error: str = ""
    said: list[str] = field(default_factory=list)  # the Fulfil's lines, as pubdeploy prints them
    needs: list[dict[str, str]] = field(default_factory=list)  # each need's state at the end
    documents: list[dict[str, Any]] = field(default_factory=list)  # the Fulfil's journals (in memory)
    approve_on_plan: str = ""  # a person's press: approved by them once planned

    @property
    def ended(self) -> bool:
        return self.state in ENDED

    @property
    def run_url(self) -> str:
        return self.requester.run_url

    def record(self) -> dict[str, Any]:
        """What the ledger keeps of it: names and states, never a value."""
        return {"id": self.id, "kind": self.kind, "requester": self.requester.record(),
                "repository": self.repository, "unit": self.unit, "created_at": self.created_at,
                "expires_at": self.expires_at, "state": self.state, "plan": self.plan, "text": self.text,
                "plan_sha256": self.plan_sha256, "changes": self.changes, "approver": self.approver,
                "error": self.error, "said": self.said[-60:], "needs": self.needs}

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> Ask:
        fields = {k: data[k] for k in cls.__dataclass_fields__ if k in data and k != "requester"}
        return cls(requester=Requester.from_record(data.get("requester") or {}), **fields)


def _iso(moment: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment))


class Approvals:
    """The engine's requests: asked, planned, approved, run and journaled."""

    def __init__(self, engine: Any, ledger: Ledger, *, hours: int = 24, log: Callable[[str], None] = print,
                 clock: Callable[[], float] = time.time, lock_seconds: float | None = None):
        self.engine, self.ledger, self.log, self.clock = engine, ledger, log, clock
        self.seconds = hours * 3600
        # How long a request's walk waits for a round or another walk of that service to end.
        self.lock_seconds = lock_seconds if lock_seconds is not None else \
            engine.settings.walk_seconds + engine.settings.fulfil_seconds
        self.asks: dict[str, Ask] = {}
        self.threads: list[threading.Thread] = []
        self._lock = threading.RLock()

    # --- The ledger

    def _event(self, ask: Ask) -> dict[str, Any]:
        last = ask.changes[-1]
        ended = sorted((a for a in self.asks.values() if a.ended), key=lambda a: a.changes[-1]["at"])[-KEEP_ENDED:]
        return {"schema": SCHEMA, "id": ask.id, "kind": ask.kind, "state": ask.state, "at": last["at"],
                "by": last["by"], "repository": ask.repository, "unit": ask.unit,
                "requester": ask.requester.record(), "sha": ask.requester.sha, "run_url": ask.run_url,
                "plan_sha256": ask.plan_sha256, "needs": ask.needs if ask.ended else [],
                "open": [a.record() for a in self.asks.values() if not a.ended],
                "ended": [a.record() for a in ended]}

    def _move(self, ask: Ask, state: str, by: str, **changed: Any) -> None:
        """One state change, journaled first: a JournalRefused leaves the request as it was."""
        with self._lock:
            before = dict(ask.__dict__)
            ask.__dict__.update(changed)
            ask.state = state
            ask.changes = [*ask.changes, {"state": state, "at": _iso(self.clock()), "by": by}]
            known = ask.id in self.asks
            self.asks[ask.id] = ask
            try:
                self.ledger.append(self._event(ask))
            except JournalRefused:
                ask.__dict__.clear()
                ask.__dict__.update(before)
                if not known:
                    del self.asks[ask.id]
                raise
        self.log(f"convergence: request {ask.id} ({ask.kind} {ask.repository} {ask.unit}): {state} by {by}")

    def _end(self, ask: Ask, state: str, by: str, **changed: Any) -> None:
        """An end the journal refused is still the end: said in the log, kept in memory."""
        try:
            self._move(ask, state, by, **changed)
        except JournalRefused as e:
            ask.__dict__.update(changed)
            ask.state, ask.error = "failed", ask.error or f"the journal refused this request's end ({e})"
            self.log(f"convergence: request {ask.id}: the journal refused its end ({e})")

    def start(self) -> None:
        """The open requests and the last ended ones, from the head; a walk the restart broke ends failed."""
        document = self.ledger.load() or {}
        for data in [*document.get("ended", []), *document.get("open", [])]:
            ask = Ask.from_record(data)
            self.asks[ask.id] = ask
        for ask in list(self.asks.values()):
            if ask.state in ("planning", "running"):
                self._end(ask, "failed", "engine", error=f"the engine restarted while it was {ask.state}")
            elif ask.state == "approved" and ask.kind == "fulfil":
                self._thread(self._run, ask)

    # --- Asking

    def ask(self, requester: Requester, body: Any) -> Ask:
        """A caller's request, taken once its first event is journaled; a Refused says why not."""
        if not isinstance(body, dict) or body.get("kind") not in KINDS:
            raise Refused(400, "bad-kind")
        now = self.clock()
        new = Ask(f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}-{secrets.token_hex(4)}", body["kind"],
                  requester, requester.repository, "", now, now + self.seconds)
        if new.kind == "gate":
            text = body.get("plan")
            if not isinstance(text, str) or not text.strip():
                raise Refused(400, "no-plan")
            if len(text.encode("utf-8")) > PLAN_BYTES:
                raise Refused(400, "plan-too-large")
            new.unit = str(body.get("unit") or "")[:64]
            return self._take(new, "awaiting", text=text, plan_sha256=plan_sha256(text))
        if requester.kind == "kubernetes":
            raise Refused(403, "gate-only")
        new.unit = str(body.get("unit") or "")
        if str(body.get("repository") or requester.repository) != requester.repository:
            raise Refused(403, "other-repository")
        reason = self.engine.can_fulfil(new.repository, new.unit)
        if reason:
            raise Refused(409, reason)
        taken = self._take(new, "planning")
        self._thread(self._plan, taken)
        return taken

    def press(self, repository: str, unit: str, actor: str) -> Ask | str:
        """A signed-in person's Fulfil from the service page: their request, approved by them."""
        reason = self.engine.can_fulfil(repository, unit)
        if reason:
            return REASONS[reason]
        now = self.clock()
        new = Ask(f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}-{secrets.token_hex(4)}", "fulfil",
                  Requester("person", actor, actor=actor), repository, unit, now, now + self.seconds,
                  approve_on_plan=actor)
        try:
            taken = self._take(new, "planning")
        except Refused:
            return "the journal refused it: nothing was started"
        self._thread(self._plan, taken)
        return taken

    def _take(self, ask: Ask, state: str, **changed: Any) -> Ask:
        try:
            self._move(ask, state, ask.requester.name, **changed)
        except JournalRefused:
            raise Refused(503, "journal-refused") from None
        return ask

    def _thread(self, target: Callable[[Ask], None], ask: Ask) -> None:
        thread = threading.Thread(target=target, args=(ask,), daemon=True, name=f"request {ask.id}")
        self.threads.append(thread)
        thread.start()

    def wait(self, timeout: float = 10.0) -> None:
        """Every request's thread, ended (the suite's)."""
        while self.threads:
            self.threads.pop(0).join(timeout)

    # --- A fulfil's walks

    def _walked(self, ask: Ask, by: str) -> list[dict[str, Any]] | str:
        """The unit's plan from a read-only walk, or why there is none."""
        walk = self.engine.check_unit(ask.repository, ask.unit, by)
        if not walk.ok:
            return walk.error
        found = [d for d in walk.documents if d["service"]["unit"] == ask.unit]
        return plan_of(found[0]) if found else "the walk printed no journal for that unit"

    def _plan(self, ask: Ask) -> None:
        lock = self.engine.walking(ask.repository)
        if not lock.acquire(timeout=self.lock_seconds):
            self._end(ask, "failed", "engine", error="a walk of that service kept running")
            return
        try:
            plan = self._walked(ask, ask.requester.name)
        except Exception as e:  # noqa: BLE001 - said on the request's page
            plan = f"the plan walk could not start ({type(e).__name__})"
        finally:
            lock.release()
        if isinstance(plan, str):
            self._end(ask, "failed", "engine", error=plan)
            return
        if not plan:
            self._end(ask, "done", "engine", plan=[], plan_sha256=plan_sha256([]), needs=[])
            return
        try:
            self._move(ask, "awaiting", "engine", plan=plan, plan_sha256=plan_sha256(plan))
        except JournalRefused as e:
            self._end(ask, "failed", "engine", error=f"the journal refused its plan ({e})")
            return
        if ask.approve_on_plan:
            self.decide(ask.id, ask.approve_on_plan, True)

    def _run(self, ask: Ask) -> None:
        lock = self.engine.walking(ask.repository)
        if not lock.acquire(timeout=self.lock_seconds):
            self._end(ask, "failed", "engine", error="a walk of that service kept running")
            return
        try:
            try:
                self._move(ask, "running", ask.approver)
            except JournalRefused as e:
                self._end(ask, "failed", "engine", error=f"the journal refused its start ({e}): nothing was run")
                return
            try:
                now = self._walked(ask, ask.approver)
                if isinstance(now, str) or plan_sha256(now) != ask.plan_sha256:
                    self._end(ask, "failed", "engine", error=CHANGED if not isinstance(now, str) else now)
                    return
                result = self.engine.fulfil_walk(ask.repository, ask.unit, ask.approver)
            except Exception as e:  # noqa: BLE001 - said on the request's page
                self._end(ask, "failed", "engine", error=f"the fulfil could not start ({type(e).__name__})")
                return
            found = [d for d in result.documents if d["service"]["unit"] == ask.unit]
            needs = needs_of(found[0]) if found else []
            if result.ok:
                self._end(ask, "done", "engine", said=result.said, needs=needs, documents=result.documents)
            else:
                self._end(ask, "failed", "engine", said=result.said, needs=needs, error=result.error)
        finally:
            lock.release()

    # --- Deciding, polling, expiring

    def get(self, id_: str) -> Ask | None:
        self.expire_due()
        return self.asks.get(id_)

    def decide(self, id_: str, actor: str, approve: bool) -> str:
        """A person's Approve or Decline of an awaiting request; "" when it counted, else why not."""
        self.expire_due()
        with self._lock:
            ask = self.asks.get(id_)
            if ask is None or ask.state != "awaiting":
                return "that request is not awaiting a decision"
            try:
                self._move(ask, "approved" if approve else "declined", actor,
                           approver=actor if approve else "")
            except JournalRefused:
                return "the journal refused it: nothing changed"
        if approve and ask.kind == "fulfil":
            self._thread(self._run, ask)
        return ""

    def expire_due(self) -> None:
        now = self.clock()
        for ask in [a for a in self.asks.values() if a.state == "awaiting" and a.expires_at <= now]:
            try:
                self._move(ask, "expired", "expiry")
            except JournalRefused as e:
                self.log(f"convergence: request {ask.id}: the journal refused its expiry ({e}); tried again later")

    def loop(self, stop: threading.Event) -> None:  # pragma: no cover - the pod's thread; expire_due() is tested
        while not stop.wait(60):
            self.expire_due()

    def awaiting(self) -> int:
        self.expire_due()
        return sum(a.state == "awaiting" for a in self.asks.values())

    def listed(self) -> tuple[list[Ask], list[Ask]]:
        """The open requests, oldest first, then the last ended ones, newest first."""
        self.expire_due()
        asks = list(self.asks.values())
        return (sorted((a for a in asks if not a.ended), key=lambda a: a.created_at),
                sorted((a for a in asks if a.ended), key=lambda a: a.changes[-1]["at"], reverse=True)[:KEEP_ENDED])

    def answer(self, ask: Ask, site: str) -> dict[str, Any]:
        """What a polling caller is told: the state and the result's summary, names only."""
        return {"id": ask.id, "kind": ask.kind, "state": ask.state, "repository": ask.repository, "unit": ask.unit,
                "plan_sha256": ask.plan_sha256, "url": f"{site}/approvals/{ask.id}",
                "expires_at": _iso(ask.expires_at), "approver": ask.approver,
                "result": {"needs": ask.needs, "error": ask.error}}

