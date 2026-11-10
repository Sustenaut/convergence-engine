"""The walks' history, read back from storage's tenant `plane`.

pubdeploy writes each walk's deploy-journal/2 document as a file of the tenant
and moves the unit's head to it, so a head's log is that unit's walk history,
newest first, from every place that walked it (this engine, a laptop, Actions).

A head's name is flat and lower case (storage allows [a-z0-9._-], 64 at most):
`<owner>.<repository>.<unit>`, as pubdeploy names it.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

TENANT = "plane"
FLAT = re.compile(r"[^a-z0-9._-]")
HASH = re.compile(r"[0-9a-f]{64}")


def head(repository: str, unit: str) -> str:
    """The head pubdeploy moves for a unit's journal."""
    owner, name = repository.lower().split("/")
    return FLAT.sub("-", f"{owner}.{name}.{unit.lower()}")[:64]


class Heads(Protocol):
    def head_log(self, name: str) -> list[dict]: ...

    def get_file(self, id: str, name: str) -> bytes: ...  # noqa: A002 - storage's own name


class Journals:
    """Storage's journals, through the integrations library's Storage client
    (its own calls are in the engine's call log)."""

    def __init__(self, storage: Heads):
        self.storage = storage

    def history(self, repository: str, unit: str, limit: int = 48) -> list[dict[str, str]]:
        """Each walk the head has pointed at, newest first: hash, time and who wrote it."""
        try:
            log = self.storage.head_log(head(repository, unit))
        except Exception as e:  # noqa: BLE001 - any refusal: the page says so and shows the rest
            if type(e).__name__ == "NotFound":
                return []
            raise
        return [{"hash": str(e["hash"]), "time": str(e["time"]), "principal": str(e.get("principal", ""))}
                for e in log[:limit] if HASH.fullmatch(str(e.get("hash", "")))]

    def journal(self, hash_: str) -> dict[str, Any]:
        if not HASH.fullmatch(hash_):
            raise ValueError("not a journal's hash")
        return json.loads(self.storage.get_file(hash_, "journal.json"))
