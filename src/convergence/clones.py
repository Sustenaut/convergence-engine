"""Shallow clones of the default branches, refreshed before each walk.

Clones sit side by side, <work>/clones/<owner>/<name>, so a service's
`--zones ../shared-infra/pubkit.json` finds its owner's zones repository the
way it does on a laptop. The installation token reaches git as an
Authorization header in git's environment (GIT_CONFIG_*), never in a remote URL,
a command line or .git/config, so nothing on disk holds it.
"""

from __future__ import annotations

import base64
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

GITHUB = "https://github.com"
TIMEOUT = 120


@dataclass(frozen=True)
class Ran:
    code: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], Path, Mapping[str, str], float], Ran]


def run(argv: list[str], cwd: Path, env: Mapping[str, str], timeout: float) -> Ran:
    """One command, with exactly `env` as its environment."""
    try:
        done = subprocess.run(argv, cwd=cwd, env=dict(env), capture_output=True, timeout=timeout, check=False,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return Ran(124, "", f"{argv[0]} ran past {timeout:.0f} s")
    except OSError as e:
        return Ran(127, "", f"{argv[0]} did not run ({type(e).__name__})")
    return Ran(done.returncode, done.stdout.decode(errors="replace"), done.stderr.decode(errors="replace"))


def git_env(token: str, base: Mapping[str, str]) -> dict[str, str]:
    """`base`, with git sending the token to github.com and asking nobody for one."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {**base, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"http.{GITHUB}/.extraheader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"}


class CloneFailed(Exception):
    pass


class Clones:
    def __init__(self, work: Path, runner: Runner = run, base_env: Mapping[str, str] | None = None):
        self.root = work / "clones"
        self.runner = runner
        self.base_env = dict(base_env if base_env is not None else
                             {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ})

    def path(self, slug: str) -> Path:
        owner, name = slug.split("/")
        return self.root / owner / name

    def refresh(self, slug: str, token: str) -> Path:
        """The clone of `slug`'s default branch, as it is on GitHub now."""
        dest = self.path(slug)
        env = git_env(token, self.base_env)
        if (dest / ".git").is_dir():
            steps = [["git", "fetch", "--quiet", "--depth", "1", "--no-tags", "origin", "HEAD"],
                     ["git", "reset", "--quiet", "--hard", "FETCH_HEAD"],
                     ["git", "clean", "--quiet", "-ffdx"]]
            cwd = dest
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            steps = [["git", "clone", "--quiet", "--depth", "1", "--no-tags", f"{GITHUB}/{slug}.git", dest.name]]
            cwd = dest.parent
        for argv in steps:
            ran = self.runner(argv, cwd, env, TIMEOUT)
            if ran.code != 0:
                # git's own words name the repository, never the header.
                said = ran.stderr.strip().splitlines()[-1:] or [f"exit {ran.code}"]
                raise CloneFailed(f"{slug}: git {argv[1]} failed: {said[0][:200]}")
        return dest
