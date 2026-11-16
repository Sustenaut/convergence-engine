"""Who asks for a request: a GitHub Actions run, a Kubernetes ServiceAccount, or a person.

    POST /requests   Authorization: Bearer <token>

A GitHub Actions OIDC token (the workflow has `id-token: write`) is an RS256 JWT
signed by one of the keys at token.actions.githubusercontent.com's JWKS. It counts
when its signature checks out, its issuer is GitHub's, its audience is this site,
it has not expired, its repository's owner is one of the configured owners, and it
was minted on the default branch (`ref` refs/heads/main). The run is recorded:
repository, repository_id, sha, workflow_ref, run_id and actor.

A Kubernetes ServiceAccount's projected token (audience this site) is checked by the
cluster's TokenReview, as the engine's own ServiceAccount (k8s/admin/token-review.yaml
lets it create TokenReviews and nothing else). It counts when the cluster says it is
authenticated for this audience and the ServiceAccount is one CONVERGENCE_REQUESTERS
lists (NAMESPACE:NAME). Such a caller may only ask for a gate.

A refusal is a `Refused` with an HTTP status and a reason's name; no token is ever in
a log, an answer or the journal.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ISSUER = "https://token.actions.githubusercontent.com"
JWKS = f"{ISSUER}/.well-known/jwks"
PLUGIN = "github.actions-oidc"
MAIN = "refs/heads/main"
KEYS_SECONDS = 3600  # how long the JWKS is reused
RETRY_SECONDS = 60  # an unknown key fetches the JWKS again, at most this often
LEEWAY = 60  # clock skew allowed on exp and nbf
SA_PREFIX = "system:serviceaccount:"


class Refused(Exception):
    """A caller that does not count: the answer's status and the reason's name."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


@dataclass(frozen=True)
class Requester:
    """Who asked: a GitHub run, a ServiceAccount, or a signed-in person."""
    kind: str  # github, kubernetes or person
    name: str  # the repository, NAMESPACE:NAME, or the person's email
    repository: str = ""
    repository_id: str = ""
    sha: str = ""
    workflow_ref: str = ""
    run_id: str = ""
    actor: str = ""

    @property
    def run_url(self) -> str:
        return f"https://github.com/{self.repository}/actions/runs/{self.run_id}" \
            if self.kind == "github" and self.repository and self.run_id.isdigit() else ""

    def same(self, other: Requester) -> bool:
        """The identity a poll must show: the same run, or the same ServiceAccount."""
        if self.kind != other.kind:
            return False
        if self.kind == "github":
            return (self.repository_id, self.run_id) == (other.repository_id, other.run_id)
        return self.name == other.name

    def record(self) -> dict[str, str]:
        return {k: v for k, v in (("kind", self.kind), ("name", self.name), ("repository", self.repository),
                                  ("repository_id", self.repository_id), ("sha", self.sha),
                                  ("workflow_ref", self.workflow_ref), ("run_id", self.run_id),
                                  ("actor", self.actor)) if v}

    @classmethod
    def from_record(cls, data: dict[str, str]) -> Requester:
        return cls(**{k: str(v) for k, v in data.items() if k in cls.__dataclass_fields__})


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _int(part: str) -> int:
    return int.from_bytes(_b64(part), "big")


def jwks_from(caller: Any) -> Callable[[], dict]:
    """GitHub's JWKS, through the integrations library's Caller (the engine's call log)."""
    def fetch() -> dict:
        from integrations.calls import Request
        response = caller.call(PLUGIN, "identity", Request("GET", JWKS, {"Accept": "application/json"}))
        if not response.ok:
            raise Refused(503, "keys-unavailable")
        return response.json()
    return fetch


class GitHubOIDC:
    """Checks a GitHub Actions OIDC token against GitHub's published keys."""

    def __init__(self, fetch: Callable[[], dict], audience: str, owners: tuple[str, ...],
                 clock: Callable[[], float] = time.time):
        self.fetch, self.audience, self.owners, self.clock = fetch, audience, owners, clock
        self._keys: dict[str, rsa.RSAPublicKey] = {}
        self._fetched = -1e18
        self._lock = threading.Lock()

    def _key(self, kid: str) -> rsa.RSAPublicKey:
        with self._lock:
            now = self.clock()
            stale = now - self._fetched > KEYS_SECONDS
            if stale or (kid not in self._keys and now - self._fetched > RETRY_SECONDS):
                try:
                    data = self.fetch()
                except Refused:
                    raise
                except Exception:  # noqa: BLE001 - GitHub did not answer: nothing is let in
                    raise Refused(503, "keys-unavailable") from None
                self._keys = {k["kid"]: rsa.RSAPublicNumbers(_int(k["e"]), _int(k["n"])).public_key()
                              for k in data.get("keys", []) if k.get("kty") == "RSA" and k.get("kid")}
                self._fetched = now
            if kid not in self._keys:
                raise Refused(401, "unknown-key")
            return self._keys[kid]

    def verify(self, token: str) -> Requester:
        try:
            head64, body64, sig64 = token.split(".")
            head, claims, signature = json.loads(_b64(head64)), json.loads(_b64(body64)), _b64(sig64)
        except ValueError:
            raise Refused(401, "not-a-token") from None
        if not isinstance(head, dict) or not isinstance(claims, dict) or head.get("alg") != "RS256":
            raise Refused(401, "not-a-token")
        try:
            self._key(str(head.get("kid", ""))).verify(signature, f"{head64}.{body64}".encode(),
                                                       padding.PKCS1v15(), hashes.SHA256())
        except Refused:
            raise
        except Exception:  # noqa: BLE001 - cryptography's InvalidSignature, or a malformed key
            raise Refused(401, "bad-signature") from None
        return self._claims(claims)

    def _claims(self, claims: dict) -> Requester:
        now = self.clock()
        if claims.get("iss") != ISSUER:
            raise Refused(401, "wrong-issuer")
        audience = claims.get("aud")
        if self.audience not in (audience if isinstance(audience, list) else [audience]):
            raise Refused(401, "wrong-audience")
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] + LEEWAY < now:
            raise Refused(401, "expired")
        if isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] - LEEWAY > now:
            raise Refused(401, "not-yet-valid")
        if claims.get("repository_owner") not in self.owners:
            raise Refused(403, "wrong-owner")
        if claims.get("ref") != MAIN:
            raise Refused(403, "not-main")
        repository = str(claims.get("repository", ""))
        if repository.split("/")[0] != claims["repository_owner"]:
            raise Refused(403, "wrong-owner")
        return Requester("github", repository, repository=repository,
                         repository_id=str(claims.get("repository_id", "")), sha=str(claims.get("sha", "")),
                         workflow_ref=str(claims.get("workflow_ref", "")), run_id=str(claims.get("run_id", "")),
                         actor=str(claims.get("actor", "")))


class TokenReview:
    """Checks a ServiceAccount's token with the cluster's TokenReview."""

    def __init__(self, post: Callable[[dict], dict], audience: str, allowed: tuple[str, ...]):
        self.post, self.audience, self.allowed = post, audience, allowed

    def verify(self, token: str) -> Requester:
        try:
            status = self.post({"apiVersion": "authentication.k8s.io/v1", "kind": "TokenReview",
                                "spec": {"token": token, "audiences": [self.audience]}}).get("status") or {}
        except Exception:  # noqa: BLE001 - the cluster did not answer: nothing is let in
            raise Refused(503, "review-unavailable") from None
        username = str((status.get("user") or {}).get("username", ""))
        if not status.get("authenticated") or self.audience not in (status.get("audiences") or []):
            raise Refused(401, "bad-token")
        if not username.startswith(SA_PREFIX):
            raise Refused(403, "not-a-requester")
        name = username.removeprefix(SA_PREFIX)
        if name not in self.allowed:
            raise Refused(403, "not-a-requester")
        return Requester("kubernetes", name)


def review_post(host: str, port: str, token_file: str, ca_file: str,
                timeout: float = 10.0) -> Callable[[dict], dict]:  # pragma: no cover - the cluster's API
    """POST a TokenReview to the cluster's API as the pod's own ServiceAccount."""
    import ssl
    import urllib.request

    def post(body: dict) -> dict:
        with open(token_file, encoding="utf-8") as f:
            own = f.read().strip()
        request = urllib.request.Request(
            f"https://{host}:{port}/apis/authentication.k8s.io/v1/tokenreviews", json.dumps(body).encode(),
            {"Authorization": f"Bearer {own}", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=ssl.create_default_context(cafile=ca_file)) as answer:
            return json.loads(answer.read())
    return post


class Callers:
    """Tells which of the two a bearer token is: a JWT whose issuer is GitHub's is a run's,
    anything else goes to the cluster's TokenReview (when there is one)."""

    def __init__(self, github: GitHubOIDC, cluster: TokenReview | None):
        self.github, self.cluster = github, cluster

    def verify(self, authorization: str) -> Requester:
        scheme, _, token = authorization.strip().partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise Refused(401, "no-token")
        if self._issuer(token) == ISSUER:
            return self.github.verify(token)
        if self.cluster is None:
            raise Refused(401, "bad-token")
        return self.cluster.verify(token)

    @staticmethod
    def _issuer(token: str) -> str:
        try:
            claims = json.loads(_b64(token.split(".")[1]))
            return str(claims.get("iss", "")) if isinstance(claims, dict) else ""
        except (ValueError, IndexError):
            return ""
