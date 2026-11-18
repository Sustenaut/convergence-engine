Feature: A page per service

  Behind the team sign-in, the engine shows every service, each unit's needs with
  what makes each ready, and the walks storage has journaled. It changes nothing.

  Background:
    Given the engine walked Sustenaut/notifier and found "k8s.namespace:notifier" ready, "github.secret:SLACK" missing as propose with fix "Open the proposal", "bitwarden.generated:cookie" drift
    And storage's log for the head "sustenaut.notifier.notifier" holds 2 walks

  Scenario: The index lists every service with its counts
    When I GET "/"
    Then the answer is 200
    And the answer includes "Sustenaut/notifier"
    And the answer includes "notifier: 1 <span class=ready>ready</span>, 1 <span class=missing>missing</span>, 1 <span class=drift>drift</span>"
    And the answer's "Cache-Control" is "private, no-store"
    And the answer's "Content-Security-Policy" is "default-src 'none'; style-src 'self' 'unsafe-inline'; font-src 'self'"
    And the answer does not include "<script"

  Scenario: The service page shows each need and what makes it ready
    When I GET "/services/Sustenaut/notifier"
    Then the answer is 200
    And the answer includes "<code>github.secret:SLACK</code>"
    And the answer includes "Open the proposal"
    And the answer includes "./with-secrets ./pubdeploy walk --fulfil --unit notifier"
    And the answer includes "Walked 2026-11-10T06:00:00Z from plane by schedule"
    And the answer links to 2 walks

  Scenario: A journaled walk opens from storage
    When I GET "/services/Sustenaut/notifier/walks/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    Then the answer is 200
    And the answer includes "Sustenaut/notifier, unit notifier"

  Scenario: Another service's journal is not shown under this one
    Given storage's file for the first walk is Sustenaut/other's
    When I GET "/services/Sustenaut/notifier/walks/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    Then the answer is 404

  Scenario Outline: Walks that are not there are 404, and storage down is 502
    Given storage <storage>
    When I GET "/services/Sustenaut/notifier/walks/<hash>"
    Then the answer is <status>

    Examples:
      | storage             | hash                                                             | status |
      | answers             | not-a-hash                                                       | 404    |
      | answers             | cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc | 404    |
      | refuses everything  | aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | 502    |

  Scenario: A history storage cannot give is said, and the needs still show
    Given storage refuses everything
    When I GET "/services/Sustenaut/notifier"
    Then the answer is 200
    And the answer includes "The walk history could not be read (RuntimeError)."
    And the answer includes "<code>github.secret:SLACK</code>"

  Scenario: A unit never journaled has no walks yet
    Given storage has no log for the head "sustenaut.notifier.notifier"
    When I GET "/services/Sustenaut/notifier"
    Then the answer is 200
    And the answer includes "No walk is journaled yet."

  Scenario: A service that is not listed is 404
    When I GET "/services/Sustenaut/nothing"
    Then the answer is 404

  Scenario: A failed try shows beside the last good walk
    Given the latest try of Sustenaut/notifier failed: "git fetch failed"
    When I GET "/services/Sustenaut/notifier"
    Then the answer is 200
    And the answer includes "failed: git fetch failed"
    And the answer includes "<code>github.secret:SLACK</code>"

  Scenario: A service never walked says so
    Given the engine lists Sustenaut/new but has not walked it
    When I GET "/services/Sustenaut/new"
    Then the answer is 200
    And the answer includes "Not walked yet."
    When I GET "/"
    Then the answer is 200
    And the answer includes "not walked yet"

  Scenario: A round that could not start shows on the index
    Given the round's problem is "no round: no GitHub token (RuntimeError)"
    When I GET "/"
    Then the answer is 200
    And the answer includes "no round: no GitHub token (RuntimeError)"

  Scenario: What a walk says is escaped
    Given the engine walked Sustenaut/notifier and found "k8s.secret:<b>x</b>" missing as human with fix "<script>alert(1)</script>"
    When I GET "/services/Sustenaut/notifier"
    Then the answer is 200
    And the answer includes "&lt;script&gt;alert(1)&lt;/script&gt;"
    And the answer does not include "<script>"

  Scenario Outline: /healthz is the engine's health
    Given the engine is <health>
    When I GET "/healthz"
    Then the answer is <status>

    Examples:
      | health    | status |
      | healthy   | 200    |
      | unhealthy | 503    |

  Scenario: The pages wear Sustenaut's theme, served by the engine itself
    When I GET "/"
    Then the answer includes "<link rel="stylesheet" href="/theme/fonts.css">"
    And the answer includes "<link rel="stylesheet" href="/theme/tokens.css">"
    And the answer includes "<link rel="stylesheet" href="/theme/base.css">"
    And the answer's "Content-Security-Policy" is "default-src 'none'; style-src 'self' 'unsafe-inline'; font-src 'self'"

  Scenario: The theme is Sustenaut's
    When theme.json is read
    Then the result's "repository" is "Sustenaut/theme"

  Scenario: The theme is pinned at a tag
    When theme.json's version is read
    Then the result matches "v*"

  Scenario: Every theme file is served as theme.json pins it
    When every file theme.json pins is fetched
    Then the result is:
      """
      []
      """

  Scenario Outline: A theme file is served as what it is
    When I GET "/theme/<path>"
    Then the answer is 200
    And the answer's "content-type" is "<type>"

    Examples:
      | path                                 | type                    |
      | fonts/nunito-latin-wght-normal.woff2 | font/woff2              |
      | tokens.css                           | text/css; charset=utf-8 |

  Scenario Outline: A file that is not the theme's is 404
    When I GET "/theme/<path>"
    Then the answer is 404

    Examples:
      | path          |
      | ../theme.json |
      | nope.css      |

  Scenario: A theme file that differs from its pin is not served
    Given a copy of the theme whose base.css differs from its pin
    When the theme is loaded
    Then the result does not include "base.css"
    And the result includes "tokens.css"
