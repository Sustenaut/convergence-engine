"""The GitHub App's installation tokens: one that may only read, for the rounds, and
one with every permission the installation has, for a person's Fulfil alone.

GitHub mints an installation token limited to the permissions the request names, so
the rounds' token cannot write even though the App may: a walk run by a round could
never dispatch a workflow or set a secret, whatever it was asked. Each token is
reused until well before it expires; the write token is minted for each Fulfil.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

PLUGIN = "github.app-token"
API = "https://api.github.com"
# What a check reads: the repositories, their Actions runs, secrets' and variables' names,
# and environments. Never any write.
READ = {"contents": "read", "metadata": "read", "actions": "read", "secrets": "read",
        "actions_variables": "read", "environments": "read"}
REUSE_SECONDS = 45 * 60


class Tokens:
    def __init__(self, caller: Any, app_id: str, private_key: str, installation_id: str,
                 sign: Callable[[str, str, int], str] | None = None, clock: Callable[[], float] = time.time):
        if sign is None:  # pragma: no cover - the pod's: cryptography signs the App's JWT
            from integrations.clients.github_app_token import signed_jwt as sign
        self.caller, self.app_id, self.key, self.installation = caller, app_id, private_key, installation_id
        self.sign, self.clock = sign, clock
        self._read: tuple[str, float] = ("", 0.0)
        self._lock = threading.Lock()

    def _mint(self, permissions: dict[str, str] | None) -> str:
        from integrations.calls import Refused, Request
        body = json.dumps({"permissions": permissions}).encode() if permissions else None
        response = self.caller.call(PLUGIN, "mint-in-app", Request(
            "POST", f"{API}/app/installations/{self.installation}/access_tokens",
            {"Authorization": f"Bearer {self.sign(self.app_id, self.key, int(self.clock()))}",
             "Accept": "application/vnd.github+json", **({"Content-Type": "application/json"} if body else {})},
            body))
        if not response.ok:
            raise Refused(PLUGIN, "mint-in-app", f"HTTP {response.status}")
        return response.json()["token"]

    def read(self) -> str:
        """The rounds' token: the read permissions alone, reused until well before it expires."""
        with self._lock:
            token, until = self._read
            if self.clock() < until:
                return token
            self._read = (self._mint(READ), self.clock() + REUSE_SECONDS)
            return self._read[0]

    def write(self) -> str:
        """A Fulfil's token: every permission the installation has, minted for that Fulfil."""
        return self._mint(None)
