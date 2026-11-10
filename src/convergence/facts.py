"""Each walk's need states, as facts for observability.

The engine says what it found, never that something is wrong: one row per need
per walk, in one Avro file per walk, put in the buffer of storage's tenant
`need-states` (inbox/need-states/). The integrations ingest pod loads the
buffer into BigQuery's `convergence.need_states`, and observability's rule
`need_drift` finds a need whose state was `ready` in one walk and is not in the
next. So the rule, its threshold and the signal are observability's, and the
notifier posts it as any other.

A file that could not be put is said in the engine's log and dropped: the
journal is the record, and the next walk's facts follow 30 minutes later.
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from typing import Any

import fastavro

TENANT = "need-states"
FOLDER = "inbox/need-states/"
_TIME = {"type": "long", "logicalType": "timestamp-micros"}
SCHEMA = fastavro.parse_schema({
    "type": "record", "name": "need_state", "fields": [
        {"name": "observed_at", "type": _TIME},
        {"name": "repository", "type": "string"},
        {"name": "unit", "type": "string"},
        {"name": "need", "type": "string"},
        {"name": "type", "type": "string"},
        {"name": "name", "type": "string"},
        {"name": "grade", "type": "string"},
        {"name": "state", "type": "string"},
        {"name": "walked_from", "type": "string"},
        {"name": "walk_started_at", "type": _TIME},
    ]})


def _time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def rows(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per need of each journal. A need never checked counts as its walk's start."""
    out = []
    for doc in documents:
        service, walk = doc["service"], doc["walk"]
        started = _time(walk["started_at"])
        for need in doc["needs"]:
            out.append({"observed_at": _time(need["checked_at"]) if need.get("checked_at") else started,
                        "repository": service["repository"], "unit": service["unit"], "need": need["id"],
                        "type": need["type"], "name": need.get("name") or "", "grade": need["grade"],
                        "state": need["state"], "walked_from": walk["from"], "walk_started_at": started})
    return out


def encode(found: list[dict[str, Any]]) -> bytes:
    """The rows as one file; the same rows always give the same bytes."""
    out = io.BytesIO()
    marker = hashlib.sha256(repr(sorted(map(repr, found))).encode()).digest()[:16]
    fastavro.writer(out, SCHEMA, found, codec="deflate", sync_marker=marker)
    return out.getvalue()


def decode(data: bytes) -> list[dict[str, Any]]:
    return list(fastavro.reader(io.BytesIO(data)))


def place(repository: str, unit: str, started_at: str, data: bytes) -> str:
    moment = _time(started_at)
    who = repository.replace("/", ".").lower()
    return (f"{FOLDER}{moment:%Y-%m-%d}/{moment:%H%M%S}-{who}.{unit}-"
            f"{hashlib.sha256(data).hexdigest()[:16]}.avro")
