"""Google credentials for the walks' gcloud, without a key (step 4c).

The pod has a projected Kubernetes token for Google, audience convergence-google-read,
traded at Google's STS for a short-lived credential of convergence-key-reader through
the Workload Identity pool shared-infra's infra/convergence-keys.tf makes. The reader
views the project: the services' accounts, their keys and their Workload Identity
bindings. No walk makes a Google key (the services hold none), so there is no write
path.

gcloud does the exchange itself from a credential file (external_account), which holds
no secret: the pool's provider, the account, and the token's path. The engine writes
it at start, and gives each walk the file and the path's gcloud configuration
directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

STS = "https://sts.googleapis.com/v1/token"
IMPERSONATE = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{account}:generateAccessToken"


@dataclass(frozen=True)
class Path_:
    """One path's provider, account and projected token file."""
    name: str  # read
    provider: str  # projects/N/locations/global/workloadIdentityPools/P/providers/NAME
    account: str
    token_file: Path


def credential(path: Path_) -> dict:
    return {"type": "external_account", "audience": f"//iam.googleapis.com/{path.provider}",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt", "token_url": STS,
            "service_account_impersonation_url": IMPERSONATE.format(account=path.account),
            "credential_source": {"file": str(path.token_file)}}


def prepare(work: Path, paths: list[Path_]) -> dict[str, dict[str, str]]:
    """Each path's credential file and gcloud directory, written under work/google;
    the environment a walk on that path gets."""
    out = {}
    for path in paths:
        base = work / "google" / path.name
        (base / "gcloud").mkdir(parents=True, exist_ok=True)
        (base / "credential.json").write_text(json.dumps(credential(path), indent=2) + "\n", encoding="utf-8")
        out[path.name] = {"CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(base / "credential.json"),
                          "CLOUDSDK_CONFIG": str(base / "gcloud"),
                          "CLOUDSDK_CORE_DISABLE_USAGE_REPORTING": "true",
                          "CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK": "true"}
    return out
