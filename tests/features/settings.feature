Feature: The services, and the engine's settings

  The state repositories say which repositories are walked: those whose entry has
  the role deploy. The engine's settings come from its environment, each checked.

  Scenario: Only the repositories with the role are services, in name order
    Given a state file '{"owner": "Sustenaut", "repos": {"notifier": {"deploy": true}, "Auth": {"deploy": true}, "pubrules": {"board": true}, "maps": {"deploy": "yes"}, "schryer/visit-ee": {"deploy": true}, "odd": []}}'
    When I list its services
    Then the result is:
      """
      ["schryer/visit-ee", "Sustenaut/Auth", "Sustenaut/notifier"]
      """

  Scenario Outline: A state file that is wrong is refused, and says why
    Given a state file '<text>'
    When I list its services
    Then it is refused, saying "<message>"

    Examples:
      | text                                                   | message                              |
      | not json                                               | state.json is not JSON               |
      | {"repos": {}}                                          | state.json has no "owner" and "repos" |
      | {"owner": "Sustenaut", "repos": {"a b": {"deploy": true}}} | state.json: 'a b' is not a repository's name |

  Scenario: The defaults are Sustenaut's
    Given no settings
    When the settings are read
    Then the result's "states" is "["Sustenaut/release-state"]"
    And the result's "zones" is "{"Sustenaut": "Sustenaut/shared-infra"}"
    And the result's "every_seconds" is "1800"
    And the result's "first_round_seconds" is "300"
    And the result's "bitwarden_token" is "null"
    And the result's "requesters" is "[]"

  Scenario: Settings name more state repositories, and a token file
    Given the setting CONVERGENCE_STATES is "Sustenaut/release-state, schryer/release-state-schryer"
    And the setting CONVERGENCE_BITWARDEN_TOKEN_FILE is "/var/run/secrets/bitwarden/read-token"
    And the setting CONVERGENCE_EVERY_MINUTES is "15"
    When the settings are read
    Then the result's "states" is "["Sustenaut/release-state", "schryer/release-state-schryer"]"
    And the result's "every_seconds" is "900"
    And the result's "bitwarden_token" is "/var/run/secrets/bitwarden/read-token"

  Scenario Outline: Settings that are wrong are refused
    Given the setting <name> is "<value>"
    When the settings are read
    Then it is refused, saying "<message>"

    Examples:
      | name                      | value               | message                                                 |
      | CONVERGENCE_STATES        | release-state       | CONVERGENCE_STATES is OWNER/NAME, not 'release-state'   |
      | CONVERGENCE_STATES        | ,                   | CONVERGENCE_STATES names no state repository            |
      | CONVERGENCE_ZONES         | nope                | CONVERGENCE_ZONES is a JSON object, owner to OWNER/NAME |
      | CONVERGENCE_ZONES         | {"a": 1}            | CONVERGENCE_ZONES is a JSON object, owner to OWNER/NAME |
      | CONVERGENCE_ZONES         | {"a": "b"}          | CONVERGENCE_ZONES is OWNER/NAME, not 'b'                |
      | CONVERGENCE_EVERY_MINUTES | 1                   | CONVERGENCE_EVERY_MINUTES is at least 5                 |
      | CONVERGENCE_REQUEST_HOURS | 0                   | CONVERGENCE_REQUEST_HOURS is 1 to 168                   |
      | CONVERGENCE_REQUESTERS    | integrations        | CONVERGENCE_REQUESTERS is NAMESPACE:NAME, comma-separated |

  Scenario: `python -m convergence services` prints a state file's services
    Given a scratch directory
    And a file "state.json" holding "{"owner": "Sustenaut", "repos": {"notifier": {"deploy": true}}}"
    When convergence runs "services state.json"
    Then it succeeds
    And it prints:
      """
      Sustenaut/notifier
      """

  Scenario: With no service, it says so
    Given a scratch directory
    And a file "state.json" holding "{"owner": "Sustenaut", "repos": {}}"
    When convergence runs "services state.json"
    Then it succeeds
    And it prints:
      """
      convergence: no repository has the role deploy
      """

  Scenario: A state file it cannot read fails
    Given a scratch directory
    And a file "state.json" holding "nope"
    When convergence runs "services state.json"
    Then the exit code is 1
    And stderr mentions "convergence: state.json is not JSON"

  Scenario: Without a command, it says how to run it
    When convergence runs with no command
    Then the exit code is 2
    And stderr mentions "python -m convergence services STATE.json: the services a state file lists"

  Scenario: secret-readers.yaml is generated from the service list
    Given a scratch directory
    And a file "state.json" holding "{"owner": "Sustenaut", "repos": {"svc": {"deploy": true}, "other": {"board": true}}}"
    And a file "co/svc/pubrules.json" holding "{"deploy": {"namespace": "svc", "units": {"eu": {"namespace": "svc-eu"}, "odd": "x"}}}"
    When convergence runs "secret-readers state.json co"
    Then it succeeds
    And it prints:
      """
      # Generated by `python -m convergence secret-readers STATE.json CHECKOUTS`: do not edit.
      # The namespaces whose Secrets the engine may read, for the k8s.secret and
      # k8s.secrets-declared checks (key names and value lengths, never a value shown): every
      # deploy service's units', and its own. kube-system and the rest stay out of reach.
      apiVersion: rbac.authorization.k8s.io/v1
      kind: RoleBinding
      metadata:
        name: convergence-secret-read
        namespace: convergence
      roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: convergence-secret-read}
      subjects: [{kind: ServiceAccount, name: app, namespace: convergence}]
      ---
      apiVersion: rbac.authorization.k8s.io/v1
      kind: RoleBinding
      metadata:
        name: convergence-secret-read
        namespace: svc
      roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: convergence-secret-read}
      subjects: [{kind: ServiceAccount, name: app, namespace: convergence}]
      ---
      apiVersion: rbac.authorization.k8s.io/v1
      kind: RoleBinding
      metadata:
        name: convergence-secret-read
        namespace: svc-eu
      roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: convergence-secret-read}
      subjects: [{kind: ServiceAccount, name: app, namespace: convergence}]
      """

  Scenario: A service not checked out beside the others fails the generation
    Given a scratch directory
    And a file "state.json" holding "{"owner": "Sustenaut", "repos": {"svc": {"deploy": true}}}"
    When convergence runs "secret-readers state.json co"
    Then the exit code is 1
    And stderr mentions "co/svc/pubrules.json could not be read: check Sustenaut/svc out beside the others"

  Scenario: The image's lock, pointed at the mirrors, keeps every hash
    Given an image lock with a GitHub release wheel and a PyPI package
    When it is pointed at the mirrors
    Then the result is:
      """
      pkg @ https://storage.sustenaut.eu/wheels/f/cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc/pkg-1.0-py3-none-any.whl \
          --hash=sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
          # via -r requirements.in
      fastapi==1.0 \
          --hash=sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd

      """

  Scenario: The Google pool and the pod's token file are settings
    Given the setting CONVERGENCE_GOOGLE_POOL is "projects/1/locations/global/workloadIdentityPools/convergence"
    When the settings are read
    Then the result's "google_pool" is "projects/1/locations/global/workloadIdentityPools/convergence"
    And the result's "google_read_token" is "/var/run/secrets/google-read/token"

  Scenario: Requests have their own settings
    Given the setting CONVERGENCE_REQUESTERS is "integrations:app, notifier:app"
    And the setting CONVERGENCE_REQUEST_OWNERS is "Sustenaut, schryer"
    And the setting CONVERGENCE_REQUEST_HOURS is "12"
    When the settings are read
    Then the result's "request_hours" is "12"
    And the result's "request_owners" is "["Sustenaut", "schryer"]"
    And the result's "requesters" is "["integrations:app", "notifier:app"]"
    And the result's "site" is "https://convergence.sustenaut.eu"

  Scenario: The pod module builds nothing without its settings
    Given the environment does not set "CONVERGENCE_SERVE"
    When the pod module is loaded
    Then the result is empty
