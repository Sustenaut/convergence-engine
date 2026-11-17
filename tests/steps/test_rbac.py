"""Steps for the engine's write role and the admission policy that bounds it."""

from __future__ import annotations

from pathlib import Path

import yaml
from pytest_bdd import parsers, scenarios, then, when

scenarios("rbac.feature")

ROOT = Path(__file__).resolve().parents[2]


@when("I read k8s/admin/write-role.yaml", target_fixture="docs")
def read_role() -> dict:
    docs = list(yaml.safe_load_all((ROOT / "k8s/admin/write-role.yaml").read_text()))
    return {d["kind"]: d for d in docs}


def rules(docs: dict) -> dict:
    return {(group, resource): rule["verbs"] for rule in docs["ClusterRole"]["rules"]
            for group in rule["apiGroups"] for resource in rule["resources"]}


@then(parsers.parse('the role grants "{resource}" in "{group}" the verbs "{verbs}"'))
def grants(docs, resource, group, verbs):
    assert rules(docs)[("" if group == "core" else group, resource)] == [v.strip() for v in verbs.split(",")]


@then("the role grants nothing else, and no RBAC, exec or namespace")
def nothing_else(docs):
    assert set(rules(docs)) == {("", "secrets"), ("apps", "deployments"), ("batch", "jobs")}
    assert docs["ClusterRoleBinding"]["subjects"] == [{"kind": "ServiceAccount", "name": "app",
                                                       "namespace": "convergence"}]


@then(parsers.parse('the policy matches "{operation}" for the groups "{groups}" by convergence/app'))
def policy_matches(docs, operation, groups):
    policy = docs["ValidatingAdmissionPolicy"]["spec"]
    (rule,) = policy["matchConstraints"]["resourceRules"]
    assert operation in rule["operations"] and rule["resources"] == ["*"]
    assert rule["apiGroups"] == ["" if g.strip() == "core" else g.strip() for g in groups.split(",")]
    assert "system:serviceaccount:convergence:app" in policy["matchConditions"][0]["expression"]
    assert docs["ValidatingAdmissionPolicyBinding"]["spec"]["validationActions"] == ["Deny"]


@then(parsers.parse('the policy refuses "{namespaces}"'))
def policy_refuses(docs, namespaces):
    (validation,) = docs["ValidatingAdmissionPolicy"]["spec"]["validations"]
    for ns in namespaces.split(","):
        assert f"'{ns.strip()}'" in validation["expression"]
