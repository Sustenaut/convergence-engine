"""One service's walk: its own ./pubdeploy, in its clone, read-only.

    ./pubdeploy check --json --journal URL [--zones ../ZONES/pubkit.json]
    ./pubsec run -- ./pubdeploy check ...          with a Bitwarden read token

`check` is a walk that cannot fulfil. A person's Fulfil (the page's button) runs
`./pubdeploy walk --fulfil --yes --unit U` instead, the one place the Bitwarden
write token's file (PUBSEC_WRITE_TOKEN_FILE) and the installation token that may
write reach a walk. The subprocess's environment is built
here from nothing: PATH, HOME, the cluster's address (so kubectl uses the
pod's ServiceAccount: pubdeploy's plane channel, PUBKIT_PLANE), the
installation token as GH_TOKEN, the storage token, who walked
(PUBKIT_WALK_BY), and the Bitwarden read token when there is one. The engine's
own environment is never passed on, and no value is on the command line.

pubdeploy prints the deploy-journal/2 document (a list of them for a block of
several units) and writes each to storage's tenant `plane` itself. What it
says on stderr names needs and states, never a value; the engine keeps its
last lines to show why a walk did not finish.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clones import Ran, Runner, run

PASSED = ("PATH", "HOME", "KUBERNETES_SERVICE_HOST", "KUBERNETES_SERVICE_PORT")
SCHEMA = "deploy-journal/2"


@dataclass
class Walk:
    """What one walk of one service gave: its journals, or why there are none."""
    repository: str
    started_at: str
    seconds: float = 0.0
    documents: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    said: list[str] = field(default_factory=list)  # pubdeploy's stderr lines, last few

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass(frozen=True)
class Credentials:
    github_token: str
    storage_token: str
    bitwarden_token: str = ""
    # The Bitwarden write token's file, on the Fulfil path alone: pubsec reads it there.
    write_token_file: str = ""
    # gcloud's credential file and configuration directory for this path (google.py).
    google: tuple[tuple[str, str], ...] = ()


def argv(zones: str | None, journal: str, through_pubsec: bool, fulfil_unit: str | None = None) -> list[str]:
    command = (["./pubdeploy", "walk", "--fulfil", "--yes", "--unit", fulfil_unit] if fulfil_unit
               else ["./pubdeploy", "check"]) + ["--json", "--journal", journal]
    if zones:
        command += ["--zones", f"../{zones}/pubkit.json"]
    return ["./pubsec", "run", "--", *command] if through_pubsec else command


def environment(base: Mapping[str, str], credentials: Credentials, by: str, cache: Path) -> dict[str, str]:
    env = {k: base[k] for k in PASSED if k in base}
    env.update({"PUBKIT_PLANE": "1", "PUBKIT_WALK_BY": by, "GH_TOKEN": credentials.github_token,
                "PUBKIT_STORAGE_TOKEN": credentials.storage_token, "XDG_CACHE_HOME": str(cache),
                "UV_CACHE_DIR": str(cache / "uv"), "GIT_TERMINAL_PROMPT": "0"})
    if credentials.bitwarden_token:
        env["BWS_ACCESS_TOKEN"] = credentials.bitwarden_token
    if credentials.write_token_file:
        env["PUBSEC_WRITE_TOKEN_FILE"] = credentials.write_token_file
    env.update(dict(credentials.google))
    return env


def documents(stdout: str) -> list[dict[str, Any]]:
    """The journals a walk printed; a ValueError when it printed none."""
    data = json.loads(stdout)
    found = data if isinstance(data, list) else [data]
    if not found or not all(isinstance(d, dict) and d.get("schema") == SCHEMA for d in found):
        raise ValueError(f"not {SCHEMA}")
    return found


def walk(repository: str, clone: Path, zones: str | None, journal: str, credentials: Credentials, *,
         by: str = "schedule", base: Mapping[str, str], cache: Path, timeout: float,
         runner: Runner = run, clock=time.monotonic, now=lambda: datetime.now(timezone.utc),
         fulfil_unit: str | None = None) -> Walk:
    result = Walk(repository, now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    if not (clone / "pubdeploy").is_file():
        result.error = "the repository has no ./pubdeploy: its pubkit sync has not run with a deploy block"
        return result
    through_pubsec = bool(credentials.bitwarden_token) and (clone / "pubsec").is_file()
    started = clock()
    ran: Ran = runner(argv(zones, journal, through_pubsec, fulfil_unit), clone,
                      environment(base, credentials, by, cache), timeout)
    result.seconds = round(clock() - started, 3)
    # A fulfil's plan and what it did, as pubdeploy prints them (names and states, no value).
    result.said = [line for line in ran.stderr.splitlines() if line.strip()][-60 if fulfil_unit else -12:]
    try:
        result.documents = documents(ran.stdout)
    except ValueError:
        last = result.said[-1] if result.said else f"exit {ran.code}"
        result.error = f"the walk printed no journal ({last[:300]})"
    return result
