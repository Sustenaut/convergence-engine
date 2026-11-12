"""The engine's settings, from its environment (k8s/engine.yaml sets them).

Every credential is a file a Secret or a projected token mounts, read when it is
used: never an environment variable of the engine, so a walk's subprocess gets
only what the engine hands it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Settings:
    # The state repositories, OWNER/NAME each: their state.json lists the services.
    states: tuple[str, ...]
    # Per owner, the repository whose pubkit.json keeps that owner's hosting zones.
    zones: dict[str, str]
    storage_url: str
    # The projected service-account token, audience storage.sustenaut.eu.
    storage_token: Path
    # The GitHub App's three values, one file each: app-id, app-key, installation-id.
    github_app: Path
    # A read-only Bitwarden machine account's access token, when there is one: the
    # walk then runs through ./pubsec run and the bitwarden.* needs are checked.
    bitwarden_token: Path | None
    work: Path
    every_seconds: int
    # How long one service's walk may take.
    walk_seconds: int


def _slug(value: str, what: str) -> str:
    if not SLUG.fullmatch(value):
        raise ValueError(f"{what} is OWNER/NAME, not {value!r}")
    return value


def settings(env: Mapping[str, str]) -> Settings:
    """The settings, each checked; a ValueError says which is wrong."""
    states = tuple(_slug(s.strip(), "CONVERGENCE_STATES") for s in
                   env.get("CONVERGENCE_STATES", "Sustenaut/release-state").split(",") if s.strip())
    if not states:
        raise ValueError("CONVERGENCE_STATES names no state repository")
    try:
        zones = json.loads(env.get("CONVERGENCE_ZONES", '{"Sustenaut": "Sustenaut/shared-infra"}'))
    except ValueError:
        raise ValueError("CONVERGENCE_ZONES is a JSON object, owner to OWNER/NAME") from None
    if not isinstance(zones, dict) or not all(isinstance(v, str) for v in zones.values()):
        raise ValueError("CONVERGENCE_ZONES is a JSON object, owner to OWNER/NAME")
    zones = {str(k): _slug(v, "CONVERGENCE_ZONES") for k, v in zones.items()}
    every = int(env.get("CONVERGENCE_EVERY_MINUTES", "30"))
    if every < 5:
        raise ValueError("CONVERGENCE_EVERY_MINUTES is at least 5")
    bitwarden = env.get("CONVERGENCE_BITWARDEN_TOKEN_FILE", "")
    return Settings(
        states=states, zones=zones,
        storage_url=env.get("CONVERGENCE_STORAGE_URL", "https://storage.sustenaut.eu").rstrip("/"),
        storage_token=Path(env.get("CONVERGENCE_STORAGE_TOKEN_FILE", "/var/run/secrets/storage/token")),
        github_app=Path(env.get("CONVERGENCE_GITHUB_APP_DIR", "/var/run/secrets/github-app")),
        bitwarden_token=Path(bitwarden) if bitwarden else None,
        work=Path(env.get("CONVERGENCE_WORK", "/work")),
        every_seconds=every * 60,
        walk_seconds=int(env.get("CONVERGENCE_WALK_SECONDS", "600")))
