Feature: A page per service

  Behind the team sign-in, the engine shows every service, each unit's needs with
  what makes each ready, and the walks storage has journaled. It changes nothing.

  Background:
    Given the engine walked Sustenaut/notifier and found "k8s.namespace:notifier" ready, "github.secret:SLACK" missing as propose with fix "Open the proposal", "bitwarden.generated:cookie" drift
    And storage's log for the head "sustenaut.notifier.notifier" holds 2 walks

  Scenario: The index lists every service with its counts
    When I open "/"
    Then the page shows "Sustenaut/notifier"
    And the page shows "notifier: 1 <span class=ready>ready</span>, 1 <span class=missing>missing</span>, 1 <span class=drift>drift</span>"
    And the page is private and runs no script

  Scenario: The service page shows each need and what makes it ready
    When I open "/services/Sustenaut/notifier"
    Then the page shows "<code>github.secret:SLACK</code>"
    And the page shows "Open the proposal"
    And the page shows "./with-secrets ./pubdeploy walk --fulfil --unit notifier"
    And the page shows "Walked 2026-11-10T06:00:00Z from plane by schedule"
    And the page shows 2 links to walks

  Scenario: A journaled walk opens from storage
    When I open the first walk of Sustenaut/notifier
    Then the page shows "Sustenaut/notifier, unit notifier"

  Scenario: Another service's journal is not shown under this one
    Given storage's file for the first walk is Sustenaut/other's
    When I open the first walk of Sustenaut/notifier
    Then the answer is 404

  Scenario Outline: Walks that are not there are 404, and storage down is 502
    Given storage <storage>
    When I open "/services/Sustenaut/notifier/walks/<hash>"
    Then the answer is <status>

    Examples:
      | storage             | hash                                                             | status |
      | answers             | not-a-hash                                                       | 404    |
      | answers             | cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc | 404    |
      | refuses everything  | aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | 502    |

  Scenario: A history storage cannot give is said, and the needs still show
    Given storage refuses everything
    When I open "/services/Sustenaut/notifier"
    Then the page shows "The walk history could not be read (RuntimeError)."
    And the page shows "<code>github.secret:SLACK</code>"

  Scenario: A unit never journaled has no walks yet
    Given storage has no log for the head "sustenaut.notifier.notifier"
    When I open "/services/Sustenaut/notifier"
    Then the page shows "No walk is journaled yet."

  Scenario: A service that is not listed is 404
    When I open "/services/Sustenaut/nothing"
    Then the answer is 404

  Scenario: A failed try shows beside the last good walk
    Given the latest try of Sustenaut/notifier failed: "git fetch failed"
    When I open "/services/Sustenaut/notifier"
    Then the page shows "failed: git fetch failed"
    And the page shows "<code>github.secret:SLACK</code>"

  Scenario: A service never walked says so
    Given the engine lists Sustenaut/new but has not walked it
    When I open "/services/Sustenaut/new"
    Then the page shows "Not walked yet."
    When I open "/"
    Then the page shows "not walked yet"

  Scenario: A round that could not start shows on the index
    Given the round's problem is "no round: no GitHub token (RuntimeError)"
    When I open "/"
    Then the page shows "no round: no GitHub token (RuntimeError)"

  Scenario: What a walk says is escaped
    Given the engine walked Sustenaut/notifier and found "k8s.secret:<b>x</b>" missing as human with fix "<script>alert(1)</script>"
    When I open "/services/Sustenaut/notifier"
    Then the page shows "&lt;script&gt;alert(1)&lt;/script&gt;"
    And the page does not show "<script>"

  Scenario Outline: /healthz is the engine's health
    Given the engine is <health>
    When I open "/healthz"
    Then the answer is <status>

    Examples:
      | health    | status |
      | healthy   | 200    |
      | unhealthy | 503    |
