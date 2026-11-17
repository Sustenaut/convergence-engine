"""The engine, its requests and its pages, against a faked pubdeploy, storage, GitHub's
keys and the cluster's TokenReview: what the fulfil and approvals scenarios share."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from convergence.approvals import Approvals, Ledger
from convergence.callers import Callers, GitHubOIDC, TokenReview
from convergence.clones import Clones
from convergence.config import settings
from convergence.engine import Engine
from convergence.journals import Journals
from convergence.page import create_app
from convergence.walker import walk

from .fakes import Runner, Storage, journal

SITE = "https://convergence.example"
WHO = "someone@example.org"
ISSUER = "https://token.actions.githubusercontent.com"
REPOSITORY = "Sustenaut/notifier"
CLUSTER_ISSUER = "https://kubernetes.default.svc.cluster.local"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def cluster_token(subject: str) -> str:
    """A token shaped as the cluster's (its issuer); the faked TokenReview says whose it is."""
    claims = {"iss": CLUSTER_ISSUER, "sub": subject, "aud": [SITE]}
    return f"{_b64(b'{\"alg\":\"RS256\"}')}.{_b64(json.dumps(claims).encode())}.c2ln"


SA_TOKEN = cluster_token("system:serviceaccount:integrations:app")


class Keys:
    """A local stand-in for GitHub's OIDC keys: a JWKS, and tokens signed with its key."""

    def __init__(self, kid: str = "test-key") -> None:
        self.kid, self.key = kid, rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.key.public_key().public_numbers()
        self.jwks = {"keys": [{"kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig",
                               "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
                               "e": _b64(numbers.e.to_bytes(3, "big"))}]}
        self.fetched = 0

    def fetch(self) -> dict:
        self.fetched += 1
        return self.jwks

    def claims(self, now: float, **changed) -> dict:
        claims = {"iss": ISSUER, "aud": SITE, "exp": int(now) + 300, "nbf": int(now) - 10, "iat": int(now) - 10,
                  "repository": REPOSITORY, "repository_owner": "Sustenaut", "repository_id": "4242",
                  "ref": "refs/heads/main", "sha": "0123456789abcdef0123", "run_id": "777",
                  "workflow_ref": f"{REPOSITORY}/.github/workflows/go-live.yml@refs/heads/main",
                  "actor": "schryer"}
        claims.update(changed)
        return {k: v for k, v in claims.items() if v is not None}

    def mint(self, claims: dict, kid: str | None = None, key=None) -> str:
        head = _b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": kid or self.kid}).encode())
        body = _b64(json.dumps(claims).encode())
        signature = (key or self.key).sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{_b64(signature)}"

    def other_key(self):
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Cluster:
    """The cluster's TokenReview: which tokens it knows, and as whom."""

    def __init__(self) -> None:
        self.known = {SA_TOKEN: "system:serviceaccount:integrations:app",
                      cluster_token("system:serviceaccount:notifier:app"): "system:serviceaccount:notifier:app",
                      cluster_token("someone"): "someone"}
        self.down = False
        self.asked: list[dict] = []

    def post(self, body: dict) -> dict:
        self.asked.append(body)
        if self.down:
            raise OSError("connection refused")
        name = self.known.get(body["spec"]["token"])
        if name is None:
            return {"status": {"authenticated": False}}
        return {"status": {"authenticated": True, "audiences": body["spec"]["audiences"], "user": {"username": name}}}


class World:
    def __init__(self, tmp: Path, fulfil: bool = True, needs: dict[str, str] | None = None) -> None:
        (tmp / "storage-token").write_text("storage-token")
        (tmp / "write-token").write_text("bws-write")
        self.now = time.time()
        self.runner, self.lines, self.storage = Runner(), [], Storage()
        self.runner.states["Sustenaut/release-state"] = json.dumps(
            {"owner": "Sustenaut", "repos": {"notifier": {"deploy": True}}})
        self.runner.walk_prints(REPOSITORY, journal(states=needs or {"k8s.secret:s": "missing"}), code=1)
        self.env = {"CONVERGENCE_STORAGE_URL": "https://storage.test", "CONVERGENCE_WORK": str(tmp),
                    "CONVERGENCE_STORAGE_TOKEN_FILE": str(tmp / "storage-token"),
                    "CONVERGENCE_BITWARDEN_WRITE_TOKEN_FILE": str(tmp / "write-token")}

        def walker(*a, **k):
            return walk(*a, **k, runner=self.runner)
        self.engine = Engine(settings(self.env), Clones(tmp, self.runner, {"PATH": "/bin"}), lambda: "ghs_read",
                             None, base_env={"PATH": "/bin"}, walker=walker, log=self.lines.append,
                             fulfil_token=(lambda: "ghs_write") if fulfil else None)
        self.keys, self.cluster = Keys(), Cluster()
        self.approvals = self.new_approvals()
        self.callers = Callers(GitHubOIDC(self.keys.fetch, SITE, ("Sustenaut",), clock=lambda: self.now),
                               TokenReview(self.cluster.post, SITE, ("integrations:app",)))
        self.client = TestClient(create_app(self.engine, Journals(self.storage), self.approvals, self.callers,
                                            site=SITE, key=b"k" * 32, clock=lambda: self.now))
        self.answer = None
        self.ask = None

    def new_approvals(self) -> Approvals:
        return Approvals(self.engine, Ledger(self.storage), hours=24, log=self.lines.append,
                         clock=lambda: self.now, lock_seconds=5)

    def walk_envs(self, fulfil: bool):
        return [env for argv, _, env in self.runner.calls if argv[0] != "git" and ("--fulfil" in argv) == fulfil]

    def wait(self) -> None:
        self.approvals.wait()

    def token(self, **changed) -> str:
        return self.keys.mint(self.keys.claims(self.now, **changed))


def person(who: str = WHO, role: bool = True, origin: str | None = SITE) -> dict[str, str]:
    """The headers oauth2-proxy sets for a signed-in person, and their browser's Origin."""
    headers = {"X-Forwarded-Email": who}
    if role:
        headers["X-Forwarded-Groups"] = "people,convergence"
    if origin:
        headers["Origin"] = origin
    return headers
