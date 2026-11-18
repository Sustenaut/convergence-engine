Feature: Live runs, need by need
  A fulfil or check asked with mode live is walked need by need: the service's own
  pubdeploy compiles its machine, the executor walks each need by itself as soon as the
  needs it comes after have ended, as many at once as the machine allows, and every
  move is an event of the run's journal, kept in storage before it counts. A person
  approves the run as a request, may switch a need between check and fulfil until then,
  and may stop it: what has not started never starts, and each need walking stops as its
  class says.

  Scenario: A live request makes a run with the service's own machine
    Given the live needs "k8s.secret:a" missing, "k8s.secret:b" missing
    When a GitHub run asks for a live fulfil
    Then the run is "awaiting", with the machine pubdeploy compiled for "fulfil" and the needs "k8s.secret:a", "k8s.secret:b" waiting
    And the request's answer links the run
    And the run's journal is in storage's head convergence.runs

  Scenario: Two independent needs walk at once
    Given the live needs "k8s.secret:a" missing, "k8s.secret:b" missing
    And each need's fulfil waits until the other's has started
    When a GitHub run asks for a live fulfil
    And the person approves the run
    Then the run ends "done" with "k8s.secret:a" ready, "k8s.secret:b" ready
    And each need was walked to check, then to fulfil as the person
    And the request ended "done"
    And a new pod reads the ended run back as it was, each need fulfilled

  Scenario: A need waits for the need it comes after
    Given the live needs "k8s.secret:a" missing, "k8s.secret:b" missing after "k8s.secret:a"
    And the fulfil of "k8s.secret:a" waits until it is let go
    When a GitHub run asks for a live fulfil
    And the person approves the run
    Then "k8s.secret:a" is "fulfilling" and "k8s.secret:b" is "waiting"
    When the fulfil of "k8s.secret:a" is let go
    Then the run ends "done" with "k8s.secret:a" ready, "k8s.secret:b" ready
    And "k8s.secret:b" started after "k8s.secret:a" was ready

  Scenario: A need that fails blocks the needs after it
    Given the live needs "k8s.secret:a" missing, "k8s.secret:b" missing after "k8s.secret:a"
    And the fulfil of "k8s.secret:a" leaves it missing
    When a GitHub run asks for a live fulfil
    And the person approves the run
    Then the run ends "failed" with "k8s.secret:a" failed, "k8s.secret:b" blocked
    And the request ended "failed", saying "not ready: k8s.secret:a failed, k8s.secret:b blocked"

  Scenario: Stop before approval declines the run
    Given the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live fulfil
    And the person stops the run
    Then the run ends "declined", and nothing was fulfilled
    And the request ended "declined"

  Scenario: Stop while it walks stops each need as its class says
    Given the live needs "k8s.secret:s" missing stop signal, "k8s.secret:h" missing stop shorten, "pg.database:c" missing stop continue, "k8s.secret:w" missing after "pg.database:c"
    And every fulfil waits until it is let go
    When a GitHub run asks for a live fulfil
    And the person approves the run
    And the needs "k8s.secret:s", "k8s.secret:h", "pg.database:c" are fulfilling
    And the person stops the run
    Then "k8s.secret:s" is "stopped", saying "stopped (signal)"
    And "k8s.secret:h" is "unknown", saying "its budget ended at the stop"
    And "pg.database:c" is still "fulfilling", and the journal says "keeps running: it migrates the database"
    When the fulfil of "pg.database:c" is let go
    Then the run ends "stopped" with "pg.database:c" ready, "k8s.secret:w" stopped
    And the request ended "stopped"
    And a walk asked of "k8s.secret:s" once the run is stopping never starts

  Scenario: A need that must finish is cancelled only by its name, typed
    Given the live needs "k8s.secret:s" missing stop signal, "pg.database:c" missing stop continue
    And every fulfil waits until it is let go
    When a GitHub run asks for a live fulfil
    And the person approves the run
    And the needs "k8s.secret:s", "pg.database:c" are fulfilling
    Then cancelling "pg.database:c" typing "pg.database" is refused with 400
    And cancelling "k8s.secret:s" typing "k8s.secret:s" is refused with 409
    And cancelling "k8s.secret:x" typing "k8s.secret:x" is refused with 404
    When the person cancels "pg.database:c" typing "pg.database:c"
    Then "pg.database:c" is "stopped", saying "cancelled"
    And the journal says "cancelled, warned: it migrates the database", by the person
    When the fulfil of "k8s.secret:s" is let go
    Then the run ends "failed" with "k8s.secret:s" ready, "pg.database:c" stopped

  Scenario: A need's mode switches before approval, which freezes it
    Given the live needs "k8s.secret:a" missing, "k8s.secret:b" missing
    When a GitHub run asks for a live fulfil
    And the person switches "k8s.secret:b" to "check"
    Then the run's plan key changed, and the request's binds to it
    And switching "k8s.secret:b" to "maybe" is refused with 400
    And switching "k8s.secret:x" to "check" is refused with 404
    When the person approves the run
    Then switching "k8s.secret:a" to "check" is refused with 409
    And the run ends "done" with "k8s.secret:a" ready, "k8s.secret:b" found
    And "k8s.secret:b" was never fulfilled

  Scenario: Events are idempotent on the need and its sequence number
    Given the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live fulfil
    And the run's executor posts the event "k8s.secret:a" from "waiting" to "checking"
    And the run's executor posts the same event again
    Then both answers are the same event, and the journal holds it once
    And the event carries the need's own sequence number
    And an event from "waiting" to "checking" now is refused with 409
    And an event out of its need's order is refused with 409
    And an event without the run's token is refused with 403
    And an event that names no need is refused with 400

  Scenario: A new pod replays the runs: one awaiting waits on, one walking ends with its executor
    Given the live needs "k8s.secret:a" missing, "k8s.secret:b" missing
    And the fulfil of "k8s.secret:a" waits until it is let go
    When a GitHub run asks for a live fulfil
    And a second GitHub run asks for a live fulfil that awaits approval
    And the person approves the run
    And the needs "k8s.secret:a" are fulfilling
    And a new pod starts the runs from storage
    Then the walking run ended "failed" with "k8s.secret:a" failed, saying "executor lost"
    And the run awaiting approval still awaits it in the new pod

  Scenario: What a run shows holds no email and no link, and an ended run may be kept
    Given the live needs "k8s.secret:a" missing
    And the check of "k8s.secret:a" finds it ready, saying "ask someone@example.org at https://example.org/x?t=1"
    When a GitHub run asks for a live check
    Then the run ends "done" with "k8s.secret:a" ready
    And the run's page answer shows "ask [someone] at [link]" and holds no email or link
    And the run's answer may be kept an hour, and asks for a poll every 2000 ms
    And a stop of the ended run answers 204

  Scenario: A live check walks each need read-only, asking no one
    Given the live needs "k8s.secret:a" missing, "k8s.secret:b" ready
    When a GitHub run asks for a live check
    Then the run ends "done" with "k8s.secret:a" found, "k8s.secret:b" ready
    And nothing was fulfilled
    And the request ended "done"

  Scenario Outline: A live fulfil with nothing the engine can fulfil ends as an inline one does
    Given the live needs <needs>
    When a GitHub run asks for a live fulfil<modes>
    Then the run ends "<state>"
    And the request ended "<state>", saying "<said>"

    Examples:
      | needs                                        | modes                               | state  | said                                         |
      | "k8s.secrets-declared:x" drift graded none   |                                     | failed | nothing the engine can fulfil; needs a person |
      | "k8s.secret:a" ready                         |                                     | done   |                                              |
      | "k8s.secret:a" missing                       | , "k8s.secret:a" to check           | done   | every need is to check                       |

  Scenario Outline: A need's walk that goes wrong fails it, and only it
    Given the live needs "k8s.secret:a" missing
    And <wrong>
    When a GitHub run asks for a live fulfil
    And the person approves the run
    Then the run ends "failed" with "k8s.secret:a" failed
    And "k8s.secret:a" says "<said>"

    Examples:
      | wrong                                              | said                                   |
      | the check of "k8s.secret:a" prints no journal      | the walk printed no journal            |
      | the check of "k8s.secret:a" names another need      | the walk's journal does not name the need |
      | the fulfil of "k8s.secret:a" runs past its budget   | ran past its budget                     |
      | the walk of "k8s.secret:a" cannot be made           | its walk could not run (RuntimeError)   |

  Scenario: A need the walk cannot judge ends unknown, and the needs after it are blocked
    Given the live needs "k8s.secret:a" missing, "k8s.secret:b" missing after "k8s.secret:a"
    And the check of "k8s.secret:a" finds it unknown
    When a GitHub run asks for a live fulfil
    And the person approves the run
    Then the run ends "failed" with "k8s.secret:a" unknown, "k8s.secret:b" blocked
    And the request ended "failed", saying "not ready: k8s.secret:b blocked"

  Scenario: A run whose only needs not ready are unknown ends done, noting them
    Given the live needs "k8s.secret:a" missing, "k8s.secret:b" missing
    And the check of "k8s.secret:a" finds it unknown
    When a GitHub run asks for a live fulfil
    And the person approves the run
    Then the run ends "done" with "k8s.secret:a" unknown, "k8s.secret:b" ready
    And the request ended "done", saying "not judged: k8s.secret:a unknown"

  Scenario: A run whose service is being walked ends with its needs blocked
    Given the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live fulfil
    And a walk of Sustenaut/notifier starts and keeps running
    And the person approves the run
    Then the run ends "failed" with "k8s.secret:a" blocked

  Scenario: A run whose plan walk cannot start ends with its request
    Given the live needs "k8s.secret:a" missing
    And a walk of Sustenaut/notifier keeps running
    When a GitHub run asks for a live check
    Then the run ends "failed"
    And the request ended "failed", saying "kept running"

  Scenario: A machine pubdeploy cannot compile fails the run
    Given the live needs "k8s.secret:a" missing
    And pubdeploy compiles no machine
    When a GitHub run asks for a live fulfil
    Then the run ends "failed"
    And the request ended "failed", saying "pubdeploy machine gave no machine"

  Scenario: A signed-in approver starts a run, and approves it by its plan's key
    Given the live needs "k8s.secret:a" missing
    When the person starts a live fulfil of the unit on the page
    Then the answer is 201, a run "awaiting" asked by the person
    And approving it with another plan key is refused with 409
    When the person approves it with its plan key
    Then the run ends "done" with "k8s.secret:a" ready
    And approving it again is refused with 409

  Scenario Outline: A run asked the wrong way is refused
    Given the live needs "k8s.secret:a" missing
    When <asker> asks <how>
    Then the answer is <status>

    Examples:
      | asker              | how                                        | status |
      | a GitHub run       | for a fulfil in mode "fast"                | 400    |
      | a GitHub run       | for a fulfil with modes "nope"             | 400    |
      | a GitHub run       | for an inline fulfil with modes            | 400    |
      | a GitHub run       | for a live fulfil of an engine with no runs | 503   |
      | the person         | to start a run of mode "fast"              | 400    |
      | the person         | to start a run with a body that is not JSON | 400   |
      | the person         | to start a run of a unit not there          | 409    |
      | the person         | to start a run on an engine with no runs    | 503    |
      | the person         | for the run "nope"                          | 404    |

  Scenario: A run awaiting approval expires with its request
    Given the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live fulfil
    And 25 hours pass
    Then the run ends "expired"

  Scenario: A run still planning is not stopped
    Given the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live fulfil, which is still planning
    Then stopping it is refused with 409

  Scenario: A journal that refuses a run's event is said in the log, and the run goes on
    Given the live needs "k8s.secret:a" missing
    And storage refuses every write once the run awaits approval
    When a GitHub run asks for a live fulfil
    Then the run is "awaiting", and the log says the journal refused an event

  Scenario Outline: A run that cannot go on past its plan ends failed, saying why
    Given the live needs "k8s.secret:a" missing
    And <wrong>
    When a GitHub run asks for a live <kind>
    Then the run ends "failed"
    And the request ended "failed", saying "<said>"

    Examples:
      | wrong                                              | kind   | said                                    |
      | pubdeploy compiles a machine that is not JSON      | fulfil | pubdeploy machine gave no machine        |
      | pubdeploy machine cannot run                       | fulfil | pubdeploy machine could not run          |
      | the journal refuses the request's awaiting         | fulfil | the journal refused its plan             |
      | the journal refuses the request's start            | check  | the journal refused its start            |

  Scenario: With a Bitwarden read token a need's walk runs through ./pubsec run
    Given the live needs "k8s.secret:a" ready
    And the engine has a Bitwarden read token
    When a GitHub run asks for a live check
    Then the run ends "done" with "k8s.secret:a" ready
    And each walk ran through ./pubsec run

  Scenario: A move a need cannot make is refused, and a stop the request refuses is said
    Given the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live fulfil
    Then an event from "waiting" to "ready" now is refused with 409
    And stopping a need that is not walking does nothing
    And a stop the request refuses answers 409
