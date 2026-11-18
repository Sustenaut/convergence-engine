"""The in-process executor of live runs: each need of a run walked by itself, as many at
once as the run's machine allows, in the engine's own threads.

A need starts once every need it comes after has ended: after one that failed, was
blocked or stopped, it is blocked (predecessor_failed) and never walks. It walks as

    ./pubdeploy walk --unit U --need NAME --budget-seconds S [--fulfil --yes] --json ...

first to check it, then, when it is found (missing or drift) and is to be fulfilled
(mode fulfil, grade auto), to fulfil it, which checks it again; a need still found after
its fulfil fails. Each walk may take its need's budget and 60 s more: past that it is
terminated, and the need fails. Each move goes to runs.transition, as a remote
executor's would through POST /runs/ID/events. One run of a service at a time: the
executor holds the service's walk lock (a round's too) for the run.

Stop, while a run walks: what has not started never starts (stopped), and a need walking
stops as its class says: signal terminates its walk at once; shorten ends its budget
now, which the walk's own wait then ends the same way (its own deadline, not a signal);
continue lets it finish (runs.stop notes it), unless a person cancels it by name.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import walker
from .approvals import detail_of
from .runs import Need, Run, Runs

GRACE = 60.0  # what a need's walk may run past its budget before it is ended
SLICE = 0.2  # how often a walk is looked at, for its deadline and a stop


class Proc(Protocol):
    def poll(self, timeout: float) -> tuple[int, str, str] | None: ...

    def terminate(self) -> None: ...


class Popen:  # pragma: no cover - the pod's: a real process; the suite's executor gets fakes
    """A walk's process: its exit code and output once it ends."""

    def __init__(self, argv: list[str], cwd: Path, env: dict[str, str]):
        self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        stdin=subprocess.DEVNULL, text=True, errors="replace")

    def poll(self, timeout: float) -> tuple[int, str, str] | None:
        try:
            out, err = self.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        return self.process.returncode, out, err

    def terminate(self) -> None:
        self.process.terminate()


@dataclass
class Walking:
    proc: Proc
    deadline: float
    ended_by: str = ""  # stop, shorten or cancel: why it was ended early


class Executor:
    def __init__(self, runs: Runs, spawn: Callable[[list[str], Path, dict[str, str]], Proc] = Popen,
                 clock: Callable[[], float] = time.monotonic, lock_seconds: float | None = None,
                 grace: float = GRACE, slice_: float = SLICE):
        self.runs, self.engine = runs, runs.engine
        self.spawn, self.clock, self.grace, self.slice = spawn, clock, grace, slice_
        self.lock_seconds = lock_seconds if lock_seconds is not None else runs.approvals.lock_seconds
        self.walking: dict[tuple[str, str], Walking] = {}
        self.threads: list[threading.Thread] = []

    def _thread(self, target: Callable[..., None], *args: Any, name: str) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True, name=name)
        self.threads = [t for t in self.threads if t.is_alive()]
        self.threads.append(thread)
        thread.start()

    def wait(self, timeout: float = 10.0) -> None:
        """Every thread, ended (the suite's)."""
        while self.threads:
            self.threads.pop(0).join(timeout)

    def start(self, run: Run) -> None:
        self._thread(self._run, run, name=f"run {run.id}")

    def _run(self, run: Run) -> None:
        lock = self.engine.walking(run.repository)
        if not lock.acquire(timeout=self.lock_seconds):
            with self.runs.lock:
                for need in run.needs.values():
                    if need.state == "waiting":
                        self.runs.transition(run, need.name, "blocked", "a walk of that service kept running")
            self.runs.ended_walking(run)
            return
        try:
            self._layers(run)
        finally:
            lock.release()
        self.runs.ended_walking(run)

    def _layers(self, run: Run) -> None:
        """Start each need whose predecessors have ended, up to `parallel` at once, until all have ended."""
        active: set[str] = set()
        with self.runs.changed:
            while True:
                for need in run.needs.values():
                    if need.state != "waiting" or need.name in active:
                        continue
                    if run.stopping:
                        self.runs.transition(run, need.name, "stopped", f"stopped by {run.stopping} before it started")
                        continue
                    before = [run.needs[a] for a in need.after if a in run.needs]
                    if any(b.state in ("failed", "blocked", "stopped") for b in before):
                        self.runs.transition(run, need.name, "blocked", "a need it comes after did not end ready")
                    elif all(b.ended for b in before) and len(active) < run.parallel:
                        active.add(need.name)
                        self._thread(self._need, run, need, active, name=f"run {run.id} {need.name}")
                if not active and all(n.ended for n in run.needs.values()):
                    return
                self.runs.changed.wait(self.slice)

    def _need(self, run: Run, need: Need, active: set[str]) -> None:
        try:
            self.runs.transition(run, need.name, "checking")
            state, said = self._walk(run, need, fulfil=False)
            if state == "found" and need.fulfils and not run.stopping:
                self.runs.transition(run, need.name, "found", said)
                self.runs.transition(run, need.name, "fulfilling", "", run.approver or "engine")
                state, said = self._walk(run, need, fulfil=True)
                if state not in ("failed", "stopped"):
                    self.runs.transition(run, need.name, "checking", said)
                    state, said = (state, said) if state != "found" else ("failed", f"still found after its fulfil: {said}")
            self.runs.transition(run, need.name, state, said)
        except Exception as e:  # noqa: BLE001 - the need fails, said on the run; the others go on
            with self.runs.lock:
                if not need.ended:
                    self.runs.transition(run, need.name, "failed", f"its walk could not run ({type(e).__name__})")
        finally:
            with self.runs.changed:
                active.discard(need.name)
                self.runs.changed.notify_all()

    def _walk(self, run: Run, need: Need, fulfil: bool) -> tuple[str, str]:
        """One walk of the need: where it leaves it (ready, found, blocked, failed or stopped) and what it said."""
        argv, cwd, env = self.engine.need_command(run.repository, run.unit, need.name, fulfil, need.seconds,
                                                  run.approver or "engine")
        key = (run.id, need.name)
        self.walking[key] = walking = Walking(self.spawn(argv, cwd, env), self.clock() + need.seconds + self.grace)
        try:
            while True:
                ended = walking.proc.poll(self.slice)
                if ended is not None:
                    break
                if self.clock() >= walking.deadline:
                    walking.proc.terminate()
                    if walking.ended_by:
                        return "stopped", f"{walking.ended_by}: its budget ended at the stop"
                    return "failed", f"it ran past its budget of {need.seconds:g} s and {self.grace:g} s more"
        finally:
            del self.walking[key]
        if walking.ended_by:
            return "stopped", f"{walking.ended_by}: its walk was ended"
        code, out, err = ended
        try:
            documents = walker.documents(out)
        except ValueError:
            last = ([line for line in err.splitlines() if line.strip()] or [f"exit {code}"])[-1]
            return "failed", f"the walk printed no journal ({last[:300]})"
        run.documents.extend(documents)
        found = [n for d in documents for n in d.get("needs", []) if n.get("id") == need.name]
        if not found:
            return "failed", "the walk's journal does not name the need"
        state = found[0].get("state")
        detail = detail_of(found[0])
        return {"ready": "ready", "missing": "found", "drift": "found", "blocked": "blocked"}.get(
            state, "failed"), (detail if state in ("ready", "missing", "drift", "blocked") else f"{state}: {detail}")

    def stop(self, run: Run, need: Need, cancel: bool = False) -> None:
        """End a need's walk as its stop class says: signal (or a cancel) at once, shorten at its budget's end now."""
        walking = self.walking.get((run.id, need.name))
        if walking is None:
            return
        if cancel or need.stop == "signal":
            walking.ended_by = "cancelled" if cancel else "stopped (signal)"
            walking.proc.terminate()
        else:
            walking.ended_by = "stopped (shorten)"
            walking.deadline = self.clock()
