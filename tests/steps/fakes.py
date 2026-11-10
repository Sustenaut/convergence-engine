"""Stand-ins the suite runs against: git and pubdeploy as a recorded runner, storage
as a dictionary, and deploy-journal/2 documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from convergence.clones import Ran

HASH = "a" * 64


def journal(repository: str = "Sustenaut/notifier", unit: str = "notifier", states: dict[str, str] | None = None,
            grades: dict[str, str] | None = None, fixes: dict[str, str] | None = None) -> dict[str, Any]:
    states = states if states is not None else {"k8s.namespace:notifier": "ready"}
    grades, fixes = grades or {}, fixes or {}
    needs = [{"id": i, "type": i.split(":")[0], "name": i.split(":", 1)[-1], "implied_by": None, "after": [],
              "grade": grades.get(i, "auto"), "state": s, "fulfilled": False, "summary": f"{i} is {s}",
              "fix": fixes.get(i, ""), "details": {}, "seconds": 0.1,
              "checked_at": "2026-11-10T06:00:01Z" if s != "unknown" else ""} for i, s in states.items()]
    counts = {s: sum(v == s for v in states.values()) for s in ("ready", "missing", "drift", "blocked", "unknown")}
    return {"schema": "deploy-journal/2",
            "service": {"repository": repository, "namespace": unit, "unit": unit, "commit": "0123456789abcdef",
                        "pubdeploy": "0.2.0"},
            "walk": {"mode": "check", "offline": False, "started_at": "2026-11-10T06:00:00Z", "seconds": 2.5,
                     "by": "schedule", "from": "plane"},
            "needs": needs, "summary": counts}


class Runner:
    """Records every command; git makes the clone's directory, and pubdeploy prints
    what `walks` holds for the repository whose clone it runs in."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, dict[str, str]]] = []
        self.walks: dict[str, Ran] = {}
        self.failing: dict[str, str] = {}  # repository -> git's last words
        self.states: dict[str, str] = {}  # state repository -> state.json

    def __call__(self, argv: list[str], cwd: Path, env, timeout: float) -> Ran:
        self.calls.append((list(argv), cwd, dict(env)))
        if argv[0] == "git":
            slug = argv[-2].removeprefix("https://github.com/").removesuffix(".git") if argv[1] == "clone" \
                else f"{cwd.parent.name}/{cwd.name}"
            if slug in self.failing:
                return Ran(128, "", f"fatal: {self.failing[slug]}\n")
            dest = cwd / argv[-1] if argv[1] == "clone" else cwd
            (dest / ".git").mkdir(parents=True, exist_ok=True)
            if slug in self.states:
                (dest / "state.json").write_text(self.states[slug])
            else:
                (dest / "pubdeploy").write_text("#!/bin/sh\n")
                (dest / "pubsec").write_text("#!/bin/sh\n")
            return Ran(0, "", "")
        slug = f"{cwd.parent.name}/{cwd.name}"
        return self.walks.get(slug, Ran(2, "", "pubdeploy: deploy: pubkit.json has no deploy block\n"))

    def walk_prints(self, repository: str, documents: Any, code: int = 0, stderr: str = "") -> None:
        self.walks[repository] = Ran(code, json.dumps(documents), stderr)


class Storage:
    """Heads, files and a buffer, as storage keeps them."""

    def __init__(self) -> None:
        self.logs: dict[str, list[dict]] = {}
        self.files: dict[str, bytes] = {}
        self.buffer: dict[str, bytes] = {}
        self.refuse = ""

    def head_log(self, name: str) -> list[dict]:
        if self.refuse:
            raise RuntimeError(self.refuse)
        if name not in self.logs:
            raise type("NotFound", (Exception,), {})("no such head")
        return self.logs[name]

    def get_file(self, id: str, name: str) -> bytes:  # noqa: A002
        if self.refuse:
            raise RuntimeError(self.refuse)
        if id not in self.files:
            raise type("NotFound", (Exception,), {})("no such file")
        return self.files[id]

    def put_buffer(self, place: str, data: bytes) -> dict:
        if self.refuse:
            raise RuntimeError(self.refuse)
        self.buffer[place] = data
        return {"place": place}
