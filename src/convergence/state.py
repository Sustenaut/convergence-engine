"""The services: the repositories a state repository's state.json gives the role `deploy`.

    {"owner": "Sustenaut",
     "repos": {"notifier": {"board": true, "protect": true, "govern": true, "deploy": true}}}

The state says whether a repository is walked; the repository's own pubrules.json
deploy block says how. A name with a slash is another owner's repository.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import SLUG

ROLE = "deploy"


def services(text: str, where: str = "state.json") -> list[str]:
    """OWNER/NAME of every repository with the role, in name order."""
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError(f"{where} is not JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("owner"), str) or not isinstance(data.get("repos"), dict):
        raise ValueError(f'{where} has no "owner" and "repos"')
    found = []
    for name, roles in data["repos"].items():
        if not isinstance(roles, dict) or roles.get(ROLE) is not True:
            continue
        slug = name if "/" in name else f"{data['owner']}/{name}"
        if not SLUG.fullmatch(slug):
            raise ValueError(f"{where}: {name!r} is not a repository's name")
        found.append(slug)
    return sorted(found, key=str.lower)


def read(clone: Path) -> list[str]:
    return services((clone / "state.json").read_text(encoding="utf-8"), f"{clone.name}/state.json")


def units(clone: Path) -> list[str]:
    """The deploy units a repository's pubrules.json names: its deploy block's namespace,
    the unit a walk takes by default, then each of its `units`, in order; none without a
    block, or with one that is not as pubdeploy reads it."""
    try:
        block = json.loads((clone / "pubrules.json").read_text(encoding="utf-8")).get("deploy")
    except (OSError, ValueError, AttributeError):
        return []
    if not isinstance(block, dict) or not isinstance(block.get("namespace"), str):
        return []
    extra = block.get("units") if isinstance(block.get("units"), dict) else {}
    return [block["namespace"], *[u for u in extra if u != block["namespace"]]]
