Feature: The live run's page
  /runs/ID is the run's page for a browser: drawn once by the engine, with the
  run's public view as a JSON data block, then kept current by the bundle
  (/static/run.js), which polls the same address for the journal after the
  last event it holds. A reader without script sees the run as it is, and the
  page refreshes every 5 s. The page runs script and connects to this site
  only.

  Scenario: Without script the page shows the run and refreshes every 5 s
    Given the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live fulfil
    And a person opens the run's page
    Then the page is HTML that refreshes every 5 s for a reader without script
    And the page shows the run's state "awaiting" and the need "k8s.secret:a"
    And the page's data block holds the run's public view, its plan_key among it
    And the page loads /static/run.js and /static/run.css

  Scenario: The run's page runs script and connects to this site only
    Given the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live fulfil
    And a person opens the run's page
    Then its Content-Security-Policy is exactly "default-src 'none'; style-src 'self' 'unsafe-inline'; font-src 'self'; script-src 'self'; connect-src 'self'"
    And the engine's other pages keep their Content-Security-Policy "default-src 'none'; style-src 'self' 'unsafe-inline'; font-src 'self'"

  Scenario: The bundle is served as JavaScript, and nothing else from /static/
    When a person asks for "/static/run.js"
    Then it answers 200 as "text/javascript; charset=utf-8", not to be sniffed
    When a person asks for "/static/run.css"
    Then it answers 200 as "text/css; charset=utf-8", not to be sniffed
    When a person asks for "/static/other.js"
    Then it answers 404

  Scenario: A poll gets JSON and only the journal after the event it names
    Given the live needs "k8s.secret:a" missing
    When a GitHub run asks for a live fulfil
    And the bundle polls the run after event 1
    Then the answer is JSON with only the events after 1
    When the bundle asks for the run with Accept: application/json
    Then the answer is JSON with every event

  Scenario: Start a run lists each service's units, and the needs of the unit's machine
    Given the live needs "k8s.secret:a" missing, "k8s.secret:b" missing
    When a person opens "/runs/new"
    Then the page's data block lists the unit "notifier" of the service
    When the bundle asks for the needs of the unit "notifier" in mode "fulfil"
    Then the needs read "k8s.secret:a fulfil, k8s.secret:b fulfil"

  Scenario: Start a run is for a signed-in person
    Given the live needs "k8s.secret:a" missing
    When someone not signed in opens "/runs/new"
    Then it answers 403

  Scenario Outline: Start a run's needs are refused, saying why
    Given the live needs "k8s.secret:a" missing
    And <how>
    When the bundle asks for the needs of the unit "<unit>" in mode "fulfil"<as>
    Then it answers <status>

    Examples:
      | how                                   | unit     | as                          | status |
      | the engine walks as it always does    | notifier | , for someone not signed in | 403    |
      | the engine walks as it always does    | other    |                             | 404    |
      | pubdeploy compiles a machine that is not JSON | notifier |                     | 409    |

  Scenario: Start a run on an engine with no live runs answers 503
    Given the live needs "k8s.secret:a" missing
    And the engine has no live runs
    When a person opens "/runs/new"
    Then it answers 503
