"""Stand-ins the suite runs against: git and pubdeploy as a recorded runner, storage
as a dictionary, and deploy-journal/2 documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from convergence.clones import Ran

HASH = "a" * 64


def journal(repository: str = "Sustenaut/notifier", unit: str = "notifier", states: dict[str, str] | None = None,
            grades: dict[str, str] | None = None, fixes: dict[str, str] | None = None,
            details: dict[str, list[str]] | None = None) -> dict[str, Any]:
    states = states if states is not None else {"k8s.namespace:notifier": "ready"}
    grades, fixes, details = grades or {}, fixes or {}, details or {}
    needs = [{"id": i, "type": i.split(":")[0], "name": i.split(":", 1)[-1], "implied_by": None, "after": [],
              "grade": grades.get(i, "auto"), "state": s, "fulfilled": False, "summary": f"{i} is {s}",
              "fix": fixes.get(i, ""), "details": details.get(i, []), "seconds": 0.1,
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
        self.fulfils: dict[str, Ran] = {}  # what a --fulfil walk prints, when it differs from a check
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
        if "--fulfil" in argv and slug in self.fulfils:
            return self.fulfils[slug]
        return self.walks.get(slug, Ran(2, "", "pubdeploy: deploy: pubrules.json has no deploy block\n"))

    def walk_prints(self, repository: str, documents: Any, code: int = 0, stderr: str = "") -> None:
        self.walks[repository] = Ran(code, json.dumps(documents), stderr)

    def fulfil_prints(self, repository: str, documents: Any, code: int = 0, stderr: str = "") -> None:
        self.fulfils[repository] = Ran(code, json.dumps(documents), stderr)

    def walked(self, flag: str) -> list[list[str]]:
        """Each pubdeploy command run with that flag (--fulfil, --unit)."""
        return [argv for argv, _, _ in self.calls if argv[0] != "git" and flag in argv]


def _not_found(said: str) -> Exception:
    return type("NotFound", (Exception,), {})(said)


class Storage:
    """Heads, files and a buffer, as storage keeps them."""

    def __init__(self) -> None:
        self.logs: dict[str, list[dict]] = {}
        self.files: dict[str, bytes] = {}
        self.buffer: dict[str, bytes] = {}
        self.refuse = ""
        self.refuse_writes = False  # every upload refused
        self.uploads = 0
        self.refused: set[int] = set()  # the numbers of the uploads refused, counting from 1

    def refuse_upload(self, later: int = 1) -> None:
        """Refuse the upload that many after the last one."""
        self.refused.add(self.uploads + later)

    def upload_file(self, data: bytes, *, name: str) -> dict:
        self.uploads += 1
        if self.refuse_writes or self.uploads in self.refused:
            raise RuntimeError("storage said 503")
        import hashlib
        hash_ = hashlib.sha256(data).hexdigest()
        self.files[hash_] = data
        return {"id": hash_, "name": name}

    def get_head(self, name: str) -> tuple[bytes, str | None]:
        if self.refuse:
            raise RuntimeError(self.refuse)
        if not self.logs.get(name):
            raise _not_found("no such head")
        hash_ = self.logs[name][0]["hash"]
        return self.files[hash_], hash_

    def move_head(self, name: str, hash: str, *, if_match: str | None = None) -> dict:  # noqa: A002
        log = self.logs.setdefault(name, [])
        if (log[0]["hash"] if log else None) != if_match:
            raise RuntimeError("storage said 412")
        log.insert(0, {"hash": hash, "time": f"t{len(log)}", "principal": "k8s:convergence/app"})
        return log[0]

    def events(self, name: str = "convergence.approvals") -> list[dict]:
        """The head's events, oldest first."""
        return [json.loads(self.files[e["hash"]]) for e in reversed(self.logs.get(name, []))]

    def head_log(self, name: str) -> list[dict]:
        if self.refuse:
            raise RuntimeError(self.refuse)
        if name not in self.logs:
            raise _not_found("no such head")
        return self.logs[name]

    def get_file(self, id: str, name: str) -> bytes:  # noqa: A002
        if self.refuse:
            raise RuntimeError(self.refuse)
        if id not in self.files:
            raise _not_found("no such file")
        return self.files[id]

    def put_buffer(self, place: str, data: bytes) -> dict:
        if self.refuse:
            raise RuntimeError(self.refuse)
        self.buffer[place] = data
        return {"place": place}
