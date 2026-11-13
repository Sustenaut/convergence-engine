"""A round: every service walked once, one after another, then the next round
`every_seconds` after this one started.

Before each round the state repositories are refreshed (the service list may have
changed), then each owner's zones repository, then each service's clone before its
walk. A service whose clone or walk fails keeps its last good walk on its page, with
the failure beside it; the round goes on to the next. One round at a time: a round
asked for while one runs is not started.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import facts, state
from .clones import CloneFailed, Clones
from .config import Settings
from .walker import Credentials, Walk, walk


class Buffer(Protocol):
    def put_buffer(self, place: str, data: bytes) -> dict: ...


@dataclass
class Service:
    repository: str
    last: Walk | None = None  # the last walk that printed journals
    failure: str = ""  # why the latest try did not, if it did not
    tried_at: str = ""


@dataclass
class Status:
    services: dict[str, Service] = field(default_factory=dict)
    round_started: float | None = None
    round_ended: float | None = None
    problem: str = ""  # a round that could not start: no token, no state


def prepare(work: Path) -> None:
    """The work directory's parts, on a fresh claim too: HOME, the caches and the clones."""
    for part in ("home", "cache", "clones"):
        (work / part).mkdir(parents=True, exist_ok=True)


def read_secret(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


class Engine:
    def __init__(self, settings: Settings, clones: Clones, github_token: Callable[[], str], buffer: Buffer | None,
                 *, base_env: Mapping[str, str], walker: Callable[..., Walk] = walk,
                 log: Callable[[str], None] = print, clock: Callable[[], float] = time.time):
        self.settings, self.clones, self.github_token, self.buffer = settings, clones, github_token, buffer
        self.base_env, self.walker, self.log, self.clock = dict(base_env), walker, log, clock
        self.status = Status()
        self.started = clock()
        self._running = threading.Lock()

    def credentials(self) -> Credentials:
        bitwarden = self.settings.bitwarden_token
        return Credentials(github_token=self.github_token(), storage_token=read_secret(self.settings.storage_token),
                           bitwarden_token=read_secret(bitwarden) if bitwarden and bitwarden.is_file() else "")

    def services(self, token: str) -> list[str]:
        found: list[str] = []
        for repository in self.settings.states:
            found += [s for s in state.read(self.clones.refresh(repository, token)) if s not in found]
        return found

    def round(self, by: str = "schedule") -> bool:
        """Walk every service once; False when a round was already running."""
        if not self._running.acquire(blocking=False):
            return False
        try:
            self.status.round_started = self.clock()
            self._round(by)
            self.status.round_ended = self.clock()
            return True
        finally:
            self._running.release()

    def _round(self, by: str) -> None:
        try:
            credentials = self.credentials()
            listed = self.services(credentials.github_token)
        except (OSError, ValueError, CloneFailed) as e:
            self.status.problem = f"no round: {e}" if not isinstance(e, OSError) else \
                f"no round: a credential file could not be read ({type(e).__name__})"
            self.log(f"convergence: {self.status.problem}")
            return
        except Exception as e:  # noqa: BLE001 - the token's mint refused: said, and tried next round
            self.status.problem = f"no round: no GitHub token ({type(e).__name__})"
            self.log(f"convergence: {self.status.problem}")
            return
        self.status.problem = ""
        self.status.services = {r: self.status.services.get(r) or Service(r) for r in listed}
        zones = self._zones(listed, credentials.github_token)
        for repository in listed:
            self._one(self.status.services[repository], zones.get(repository.split("/")[0]), credentials, by)

    def _zones(self, listed: list[str], token: str) -> dict[str, str]:
        """Per owner, the name of its zones repository's clone beside the services'."""
        out = {}
        for owner in sorted({r.split("/")[0] for r in listed}):
            repository = self.settings.zones.get(owner)
            if repository is None or repository.split("/")[0] != owner:
                continue
            try:
                self.clones.refresh(repository, token)
                out[owner] = repository.split("/")[1]
            except CloneFailed as e:
                self.log(f"convergence: no zones for {owner}: {e}")
        return out

    def _one(self, service: Service, zones: str | None, credentials: Credentials, by: str) -> None:
        try:
            clone = self.clones.refresh(service.repository, credentials.github_token)
        except CloneFailed as e:
            service.failure, service.tried_at = str(e), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))
            self.log(f"convergence: {service.failure}")
            return
        # Read again for each walk: the kubelet renews the projected token, and a round is long.
        credentials = dataclasses.replace(credentials, storage_token=read_secret(self.settings.storage_token))
        result = self.walker(service.repository, clone, zones, self.settings.storage_url, credentials, by=by,
                             base=self.base_env, cache=self.settings.work / "cache",
                             timeout=self.settings.walk_seconds)
        service.tried_at = result.started_at
        if not result.ok:
            service.failure = result.error
            self.log(f"convergence: {service.repository}: {result.error}")
            return
        service.last, service.failure = result, ""
        for document in result.documents:
            counts = document.get("summary", {})
            said = ", ".join(f"{counts[s]} {s}" for s in ("ready", "missing", "drift", "blocked", "unknown")
                             if counts.get(s))
            self.log(f"convergence: {service.repository} {document['service']['unit']}: {said or 'no needs'}")
            self._publish(document)

    def _publish(self, document: dict[str, Any]) -> None:
        if self.buffer is None:
            return
        data = facts.encode(facts.rows([document]))
        service = document["service"]
        try:
            self.buffer.put_buffer(facts.place(service["repository"], service["unit"],
                                               document["walk"]["started_at"], data), data)
        except Exception as e:  # noqa: BLE001 - facts are best effort; the journal is the record
            self.log(f"convergence: {service['repository']} {service['unit']}: no facts put ({type(e).__name__})")

    def healthy(self) -> bool:
        """A round has ended within a round's wait and the longest round this many
        services can take (each walk its whole budget), or the engine is that new."""
        walks = max(1, len(self.status.services))
        allowed = (2 * self.settings.every_seconds + walks * self.settings.walk_seconds
                   + self.settings.first_round_seconds)
        last = self.status.round_ended if self.status.round_ended is not None else self.started
        return self.clock() - last <= allowed

    def loop(self, stop: threading.Event) -> None:  # pragma: no cover - the pod's thread; round() is tested
        # A new pod is usually a deploy of the engine, whose run is still finishing: walked
        # at once, its own deploy.rollout and service.health would be judged mid-deploy.
        stop.wait(self.settings.first_round_seconds)
        while not stop.is_set():
            began = self.clock()
            self.round()
            stop.wait(max(0.0, self.settings.every_seconds - (self.clock() - began)))
