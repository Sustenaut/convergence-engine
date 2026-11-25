Feature: Rounds of read-only walks

  Every 30 minutes the engine refreshes the state repositories, lists the
  repositories with the role deploy, and walks each with its own ./pubdeploy, in
  a shallow clone beside its owner's zones repository. Each walk's need states
  are put in storage as facts, for observability's need_drift rule.

  Background:
    Given the state repository Sustenaut/release-state gives the role deploy to "notifier, observability"

  Scenario: Every service is walked, and its facts put
    Given the walk of Sustenaut/notifier finds "k8s.namespace:notifier" ready and "github.secret:SLACK" missing
    And the walk of Sustenaut/observability finds "k8s.namespace:observability" ready
    When a round runs
    Then Sustenaut/notifier's last walk has 2 needs
    And Sustenaut/observability's last walk has 1 needs
    And it prints a line matching "convergence: Sustenaut/notifier notifier: 1 ready, 1 missing"
    And storage's buffer holds the facts of 2 walks
    And the facts of Sustenaut/notifier say "github.secret:SLACK" is missing

  Scenario: Each walk runs read-only, in its clone, beside the owner's zones
    Given the walk of Sustenaut/notifier finds "k8s.namespace:notifier" ready
    When a round runs
    Then Sustenaut/shared-infra was cloned
    And Sustenaut/notifier was walked with "./pubdeploy check --json --journal https://storage.test --zones ../shared-infra/pubrules.json"

  Scenario: An owner without a zones repository walks without --zones
    Given the zones repositories are '{"Other": "Other/infra", "Sustenaut": "Elsewhere/infra"}'
    And the walk of Sustenaut/notifier finds "k8s.namespace:notifier" ready
    When a round runs
    Then Sustenaut/notifier was walked with "./pubdeploy check --json --journal https://storage.test"

  Scenario: A zones repository that cannot be cloned is said, and the walks go on
    Given git cannot clone Sustenaut/shared-infra: "repository not found"
    And the walk of Sustenaut/notifier finds "k8s.namespace:notifier" ready
    When a round runs
    Then it prints a line matching "convergence: no zones for Sustenaut: Sustenaut/shared-infra: git clone failed: fatal: repository not found"
    And Sustenaut/notifier was walked with "./pubdeploy check --json --journal https://storage.test"

  Scenario: With a Bitwarden read token, the walk runs through ./pubsec run
    Given a Bitwarden read token
    And the walk of Sustenaut/notifier finds "bitwarden.secret:slack" ready
    When a round runs
    Then Sustenaut/notifier was walked with "./pubsec run -- ./pubdeploy check --json --journal https://storage.test --zones ../shared-infra/pubrules.json"
    And Sustenaut/notifier's walk had BWS_ACCESS_TOKEN

  Scenario: A clone that fails keeps the last good walk, and says why
    Given the walk of Sustenaut/notifier finds "k8s.namespace:notifier" ready
    And a round has run
    And git cannot clone Sustenaut/notifier: "could not read from remote"
    When a round runs
    Then Sustenaut/notifier's last walk has 1 needs
    And Sustenaut/notifier's failure says "git fetch failed: fatal: could not read from remote"

  Scenario: A walk that prints no journal is a failure, with pubdeploy's last line
    Given the walk of Sustenaut/notifier prints nothing and says "pubdeploy: deploy: no unit"
    When a round runs
    Then Sustenaut/notifier's failure says "the walk printed no journal (pubdeploy: deploy: no unit)"
    And Sustenaut/notifier has no last walk

  Scenario: A block of several units gives a journal per unit
    Given the walk of Sustenaut/notifier finds units "app, worker"
    When a round runs
    Then Sustenaut/notifier's last walk has 2 journals
    And storage's buffer holds the facts of 2 walks

  Scenario: Facts that storage refuses are said, and the walk still counts
    Given storage refuses everything
    And the walk of Sustenaut/notifier finds "k8s.namespace:notifier" ready
    When a round runs
    Then it prints a line matching "convergence: Sustenaut/notifier notifier: no facts put (RuntimeError)"
    And Sustenaut/notifier's last walk has 1 needs

  Scenario: Without a buffer, no facts are put
    Given the engine has no buffer
    And the walk of Sustenaut/notifier finds "k8s.namespace:notifier" ready
    When a round runs
    Then Sustenaut/notifier's last walk has 1 needs

  Scenario: A walk with no needs says so
    Given the walk of Sustenaut/notifier finds no needs
    When a round runs
    Then it prints a line matching "convergence: Sustenaut/notifier notifier: no needs"

  Scenario: A state repository that cannot be cloned stops the round
    Given git cannot clone Sustenaut/release-state: "repository not found"
    When a round runs
    Then the round's problem is "no round: Sustenaut/release-state: git clone failed: fatal: repository not found"

  Scenario: No GitHub token stops the round
    Given GitHub refuses the token
    When a round runs
    Then the round's problem is "no round: no GitHub token (RuntimeError)"

  Scenario: An unreadable storage token stops the round
    Given the storage token file is missing
    When a round runs
    Then the round's problem is "no round: a credential file could not be read (FileNotFoundError)"

  Scenario: One round at a time
    Given a round is running
    When a round is asked for
    Then the result is "false"

  Scenario Outline: The engine is healthy while rounds end in time
    Given the engine started <started> minutes ago and its last round ended <ended> minutes ago
    When the engine's health is asked
    Then the result is "<healthy>"

    Examples:
      | started | ended | healthy |
      | 5       | none  | true    |
      | 80      | none  | false   |
      | 200     | 40    | true    |
      | 200     | 80    | false   |

  Scenario: Many services give a round more time before it counts as stuck
    Given a round listed 8 services
    And the engine started 200 minutes ago and its last round ended 130 minutes ago
    When the engine's health is asked
    Then the result is "true"

  Scenario: A fresh work claim gets its home, caches and clones
    Given a scratch directory
    When the work directory "fresh" is prepared
    Then the directory "fresh" holds:
      """
      cache/
      clones/
      home/
      """

  Scenario: Rounds do not overlap across threads
    Given a round is running
    When a round is asked for from another thread
    Then the result is "false"

  Scenario: A new engine lists the services and their deploy units before its first round
    Given Sustenaut/notifier's pubrules.json deploys the namespace "notifier" and the units "notifier-eu"
    When the engine starts and lists the services
    Then it lists Sustenaut/notifier with the units "notifier, notifier-eu"
    And it lists Sustenaut/observability with no units
    And nothing was walked

  Scenario: A round reads each service's deploy units again
    Given Sustenaut/notifier's pubrules.json deploys the namespace "notifier" and the units "notifier-eu"
    And the engine starts and lists the services
    And Sustenaut/notifier's pubrules.json deploys the namespace "notifier" and the units "notifier-eu, notifier-us"
    When a round runs
    Then it lists Sustenaut/notifier with the units "notifier, notifier-eu, notifier-us"

  Scenario: A pubrules.json without a deploy block, or not JSON, names no unit
    Given Sustenaut/notifier's pubrules.json is "{"kind": "service"}"
    And Sustenaut/observability's pubrules.json is "not JSON"
    When the engine starts and lists the services
    Then it lists Sustenaut/notifier with no units
    And it lists Sustenaut/observability with no units

  Scenario: A new engine that cannot list the services says so, and its first round tries again
    Given git cannot clone Sustenaut/release-state: "repository not found"
    When the engine starts and lists the services
    Then the engine's problem reads "not listed yet"
    And it prints a line matching "convergence: the services could not be listed at start (CloneFailed)"

  Scenario: A service whose clone fails is listed, with no units
    Given git cannot clone Sustenaut/notifier: "repository not found"
    When the engine starts and lists the services
    Then it lists Sustenaut/notifier with no units
