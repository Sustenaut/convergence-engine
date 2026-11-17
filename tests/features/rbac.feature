Feature: What the engine may change in the cluster
  A Fulfil writes as convergence/app: k8s/admin/write-role.yaml is all it may do,
  and the admission policy refuses every write in the system namespaces and the
  engine's own, whatever the role allows.

  Scenario: The write role grants exactly the writes the need types use
    When I read k8s/admin/write-role.yaml
    Then the role grants "secrets" in "core" the verbs "create, update, patch"
    And the role grants "deployments" in "apps" the verbs "patch"
    And the role grants "jobs" in "batch" the verbs "create, delete"
    And the role grants nothing else, and no RBAC, exec or namespace

  Scenario: The admission policy refuses deletes in the system namespaces and the engine's own
    When I read k8s/admin/write-role.yaml
    Then the policy matches "DELETE" for the groups "core, apps, batch" by convergence/app
    And the policy refuses "kube-system, kube-public, kube-node-lease, convergence"
