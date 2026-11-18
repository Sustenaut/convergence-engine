"""Steps for the engine's write role and the admission policy that bounds it."""

from __future__ import annotations

from pathlib import Path

import yaml
from pytest_bdd import scenarios, when

scenarios("rbac.feature")

ROOT = Path(__file__).resolve().parents[2]


def _by_kind() -> dict:
    return {d["kind"]: d for d in yaml.safe_load_all((ROOT / "k8s/admin/write-role.yaml").read_text())}


@when("I read k8s/admin/write-role.yaml")
def read_role(ctx) -> None:
    ctx.attempt(_by_kind)
