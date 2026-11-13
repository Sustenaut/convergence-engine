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
    And the log says "convergence: Sustenaut/notifier notifier: 1 ready, 1 missing"
    And storage's buffer holds the facts of 2 walks
    And the facts of Sustenaut/notifier say "github.secret:SLACK" is missing

  Scenario: Each walk runs read-only, in its clone, beside the owner's zones
    Given the walk of Sustenaut/notifier finds "k8s.namespace:notifier" ready
    When a round runs
    Then Sustenaut/shared-infra was cloned
    And Sustenaut/notifier was walked with "./pubdeploy check --json --journal https://storage.test --zones ../shared-infra/pubkit.json"

  Scenario: An owner without a zones repository walks without --zones
    Given the zones repositories are '{"Other": "Other/infra", "Sustenaut": "Elsewhere/infra"}'
    And the walk of Sustenaut/notifier finds "k8s.namespace:notifier" ready
    When a round runs
    Then Sustenaut/notifier was walked with "./pubdeploy check --json --journal https://storage.test"

  Scenario: A zones repository that cannot be cloned is said, and the walks go on
    Given git cannot clone Sustenaut/shared-infra: "repository not found"
    And the walk of Sustenaut/notifier finds "k8s.namespace:notifier" ready
    When a round runs
    Then the log says "convergence: no zones for Sustenaut: Sustenaut/shared-infra: git clone failed: fatal: repository not found"
    And Sustenaut/notifier was walked with "./pubdeploy check --json --journal https://storage.test"

  Scenario: With a Bitwarden read token, the walk runs through ./pubsec run
    Given a Bitwarden read token
    And the walk of Sustenaut/notifier finds "bitwarden.secret:slack" ready
    When a round runs
    Then Sustenaut/notifier was walked with "./pubsec run -- ./pubdeploy check --json --journal https://storage.test --zones ../shared-infra/pubkit.json"
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
    Then the log says "convergence: Sustenaut/notifier notifier: no facts put (RuntimeError)"
    And Sustenaut/notifier's last walk has 1 needs

  Scenario: Without a buffer, no facts are put
    Given the engine has no buffer
    And the walk of Sustenaut/notifier finds "k8s.namespace:notifier" ready
    When a round runs
    Then Sustenaut/notifier's last walk has 1 needs

  Scenario: A walk with no needs says so
    Given the walk of Sustenaut/notifier finds no needs
    When a round runs
    Then the log says "convergence: Sustenaut/notifier notifier: no needs"

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
    Then it is not started

  Scenario Outline: The engine is healthy while rounds end in time
    Given the engine started <started> minutes ago and its last round ended <ended> minutes ago
    Then the engine is <health>

    Examples:
      | started | ended | health    |
      | 5       | none  | healthy   |
      | 80      | none  | unhealthy |
      | 200     | 40    | healthy   |
      | 200     | 80    | unhealthy |

  Scenario: Many services give a round more time before it counts as stuck
    Given a round listed 8 services
    And the engine started 200 minutes ago and its last round ended 130 minutes ago
    Then the engine is healthy

  Scenario: A fresh work claim gets its home, caches and clones
    When the work directory is prepared
    Then it has "home, cache, clones"
