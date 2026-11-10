Feature: The services, and the engine's settings

  The state repositories say which repositories are walked: those whose entry has
  the role deploy. The engine's settings come from its environment, each checked.

  Scenario: Only the repositories with the role are services, in name order
    Given a state file '{"owner": "Sustenaut", "repos": {"notifier": {"deploy": true}, "Auth": {"deploy": true}, "pubkit": {"board": true}, "maps": {"deploy": "yes"}, "schryer/visit-ee": {"deploy": true}, "odd": []}}'
    When I list its services
    Then the services are "schryer/visit-ee, Sustenaut/Auth, Sustenaut/notifier"

  Scenario Outline: A state file that is wrong is refused, and says why
    Given a state file '<text>'
    When I list its services
    Then it is refused with "<message>"

    Examples:
      | text                                                   | message                              |
      | not json                                               | state.json is not JSON               |
      | {"repos": {}}                                          | state.json has no "owner" and "repos" |
      | {"owner": "Sustenaut", "repos": {"a b": {"deploy": true}}} | state.json: 'a b' is not a repository's name |

  Scenario: The defaults are Sustenaut's
    Given no settings
    Then the state repositories are "Sustenaut/release-state"
    And Sustenaut's zones are in "Sustenaut/shared-infra"
    And a round comes every 1800 seconds
    And there is no Bitwarden token file

  Scenario: Settings name more state repositories, and a token file
    Given the setting CONVERGENCE_STATES is "Sustenaut/release-state, schryer/release-state-schryer"
    And the setting CONVERGENCE_BITWARDEN_TOKEN_FILE is "/var/run/secrets/bitwarden/read-token"
    And the setting CONVERGENCE_EVERY_MINUTES is "15"
    Then the state repositories are "Sustenaut/release-state, schryer/release-state-schryer"
    And a round comes every 900 seconds
    And the Bitwarden token file is "/var/run/secrets/bitwarden/read-token"

  Scenario Outline: Settings that are wrong are refused
    Given the setting <name> is "<value>"
    Then the settings are refused with "<message>"

    Examples:
      | name                      | value               | message                                                 |
      | CONVERGENCE_STATES        | release-state       | CONVERGENCE_STATES is OWNER/NAME, not 'release-state'   |
      | CONVERGENCE_STATES        | ,                   | CONVERGENCE_STATES names no state repository            |
      | CONVERGENCE_ZONES         | nope                | CONVERGENCE_ZONES is a JSON object, owner to OWNER/NAME |
      | CONVERGENCE_ZONES         | {"a": 1}            | CONVERGENCE_ZONES is a JSON object, owner to OWNER/NAME |
      | CONVERGENCE_ZONES         | {"a": "b"}          | CONVERGENCE_ZONES is OWNER/NAME, not 'b'                |
      | CONVERGENCE_EVERY_MINUTES | 1                   | CONVERGENCE_EVERY_MINUTES is at least 5                 |

  Scenario: `python -m convergence services` prints a state file's services
    Given a state file '{"owner": "Sustenaut", "repos": {"notifier": {"deploy": true}}}' on disk
    When I run convergence "services" on it
    Then it succeeds and prints "Sustenaut/notifier"

  Scenario: With no service, it says so
    Given a state file '{"owner": "Sustenaut", "repos": {}}' on disk
    When I run convergence "services" on it
    Then it succeeds and prints "convergence: no repository has the role deploy"

  Scenario: A state file it cannot read fails
    Given a state file 'nope' on disk
    When I run convergence "services" on it
    Then it fails with 1

  Scenario: Without a command, it says how to run it
    When I run convergence "" on nothing
    Then it fails with 2

  Scenario: secret-readers.yaml is generated from the service list
    Given a state file '{"owner": "Sustenaut", "repos": {"svc": {"deploy": true}, "other": {"board": true}}}' on disk
    And a checkout of svc whose deploy block has the namespace "svc" and a unit in "svc-eu"
    When I run convergence "secret-readers" on it with the checkouts
    Then it succeeds and binds the namespaces "convergence, svc, svc-eu"

  Scenario: A service not checked out beside the others fails the generation
    Given a state file '{"owner": "Sustenaut", "repos": {"svc": {"deploy": true}}}' on disk
    When I run convergence "secret-readers" on it with the checkouts
    Then it fails with 1

  Scenario: The image's lock, pointed at the mirrors, keeps every hash
    Given an image lock with a GitHub release wheel and a PyPI package
    When it is pointed at the mirrors
    Then the wheel comes from "https://storage.sustenaut.eu/wheels/f/<its sha256>/pkg-1.0-py3-none-any.whl"
    And the PyPI package and every hash are as they were
