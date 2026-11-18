Feature: What the engine may change in the cluster
  A Fulfil writes as convergence/app: k8s/admin/write-role.yaml is all it may do,
  and the admission policy refuses every write in the system namespaces and the
  engine's own, whatever the role allows.

  Scenario: The write role grants exactly the writes the need types use
    When I read k8s/admin/write-role.yaml
    Then the result's "ClusterRole.rules" is "[{"apiGroups": [""], "resources": ["secrets"], "verbs": ["create", "update", "patch"]}, {"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["patch"]}, {"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["create", "delete"]}]"
    And the result's "ClusterRoleBinding.subjects" is "[{"kind": "ServiceAccount", "name": "app", "namespace": "convergence"}]"

  Scenario: The admission policy refuses deletes in the system namespaces and the engine's own
    When I read k8s/admin/write-role.yaml
    Then the result's "ValidatingAdmissionPolicy.spec.matchConstraints.resourceRules" is "[{"apiGroups": ["", "apps", "batch"], "apiVersions": ["*"], "operations": ["CREATE", "UPDATE", "DELETE", "CONNECT"], "resources": ["*"]}]"
    And the result's "ValidatingAdmissionPolicy.spec.matchConditions.0.expression" is "request.userInfo.username == 'system:serviceaccount:convergence:app'"
    And the result's "ValidatingAdmissionPolicy.spec.validations.0.expression" is "!(request.namespace in ['kube-system', 'kube-public', 'kube-node-lease', 'convergence'])"
    And the result's "ValidatingAdmissionPolicyBinding.spec.validationActions" is "["Deny"]"
