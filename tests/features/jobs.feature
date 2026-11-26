Feature: Live runs walked by a Job in the service's namespace
  With the Job executor, an approved run is walked by a Kubernetes Job, not by the engine:
  the engine makes the Job run-<id> in the namespace of the run's unit, and beside it a
  Secret holding only the run's token, which the Job owns. The Job runs as the namespace's
  own ServiceAccount, with the namespace's own credentials, checks the repository out at
  the commit of main the engine records on the run, and runs that commit's ./pubdeploy
  executor, which reads the run and posts its moves with the run's token. The engine holds
  the service's walk lock meanwhile, and watches the Job: one that failed or is gone before
  its executor posted its exit ends the run, executor lost.

  Scenario: An approved run makes its Job, exactly as written down, and its token's Secret
    Given the Job executor, and the live needs "k8s.secret:a" missing, "k8s.secret:b" missing after "k8s.secret:a"
    When a GitHub run asks for a live fulfil
    And the person approves the run
    Then the engine made the Job, as tests/golden/job.json has it for the run
    And it made the Secret "run-<id>" in "notifier", owned by the Job, holding only the run's token
    And neither names a credential of the engine's
    And the run records the commit "cccccccccccccccccccccccccccccccccccccccc" and the image "ghcr.io/sustenaut/convergence-executor:1"
    And the journal notes "its Job run-<id> walks commit cccccccccccc in notifier"
    And nothing was walked by the engine itself

  Scenario: The Job's executor walks the run with its token, and the run ends as it reports
    Given the Job executor, and the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live check
    And the run's executor posts the event "k8s.secret:a" from "waiting" to "checking"
    And the run's executor posts the event "k8s.secret:a" from "checking" to "ready"
    And the run's executor posts its exit 0
    And its Job completes
    Then the run ends "done" with "k8s.secret:a" ready
    And the service's walk lock is free again
    And a Job that ends now changes nothing on the ended run

  Scenario: A run's token reads and moves its own run only
    Given the Job executor, and the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live check
    And the run's executor posts its exit 0
    And a second GitHub run asks for a live check
    Then the first run's token is refused on the second run's executor read and events, with 403

  Scenario: Stop reaches the Job through the run, which its executor reads
    Given the Job executor, and the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live check
    And the run's executor posts the event "k8s.secret:a" from "waiting" to "checking"
    And the person stops the run
    Then the run, read with its token, says it is stopping
    And the Job is still there
    When the run's executor posts the event "k8s.secret:a" from "checking" to "stopped"
    And the run's executor posts its exit 0
    Then the run ends "stopped" with "k8s.secret:a" stopped

  Scenario Outline: A Job that ends before its executor's exit ends the run, executor lost
    Given the Job executor, and the live needs "k8s.secret:a" missing, "k8s.secret:b" missing after "k8s.secret:a"
    When a GitHub run asks for a live check
    And the run's executor posts the event "k8s.secret:a" from "waiting" to "checking"
    And its Job <ends>
    Then the run ends "failed" with "k8s.secret:a" failed, "k8s.secret:b" stopped
    And "k8s.secret:a" is "failed", saying "executor lost: <why>"
    And the service's walk lock is free again

    Examples:
      | ends                         | why                                         |
      | fails, as DeadlineExceeded   | its Job failed (DeadlineExceeded)           |
      | fails, saying no reason      | its Job failed (no reason given)            |
      | completes                    | its Job ended without the executor's exit   |
      | is gone                      | its Job is gone                             |
      | cannot be read, again and again | its Job could not be read (RuntimeError) |

  Scenario: A Job the API server could not be asked about once is asked again
    Given the Job executor, and the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live check
    And its Job cannot be read once
    And the run's executor posts the event "k8s.secret:a" from "waiting" to "checking"
    And the run's executor posts the event "k8s.secret:a" from "checking" to "ready"
    And the run's executor posts its exit 0
    Then the run ends "done" with "k8s.secret:a" ready

  Scenario Outline: A Job that cannot be made ends the run, and nothing walks
    Given the Job executor, and the live needs "k8s.secret:a" missing
    And <cause>
    When a GitHub run asks for a live check
    Then the run ends "failed" with "k8s.secret:a" stopped
    And "k8s.secret:a" is "stopped", saying "executor lost: its Job could not be made (<why>"
    And the service's walk lock is free again

    Examples:
      | cause                                         | why                                                    |
      | the cluster refuses to make it                | RuntimeError: jobs.batch is forbidden                  |
      | the service's deploy block names no such unit | ValueError: pubrules.json gives the unit notifier no namespace |
      | the clone's commit cannot be read             | ValueError: the clone's commit could not be read       |
      | the engine names no executor image            | ValueError: the engine names no executor image         |

  Scenario: A run whose service is being walked waits, then its needs are blocked, and no Job is made
    Given the Job executor, and the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live fulfil
    And a walk of Sustenaut/notifier starts and keeps running
    And the person approves the run
    Then the run ends "failed" with "k8s.secret:a" blocked
    And no Job was made
