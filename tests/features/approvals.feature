Feature: Requests a person approves first
  A GitHub run or a listed ServiceAccount asks with a bearer token; a person with the
  role convergence approves or declines on the engine's page; every state change is
  one event on storage's head convergence.approvals, and nothing counts that the
  journal refused.

  Background:
    Given the engine has walked Sustenaut/notifier and found "k8s.secret:s" missing

  Scenario: A run on main of an owner's repository is let in and recorded
    When a GitHub run asks for a gate with the plan "Deploy notifier 1.2"
    Then the answer is 201 and the request is "awaiting"
    And the request records the run's repository, sha, workflow and run
    And the journal holds 1 event, "awaiting"

  Scenario Outline: A GitHub token that does not count is refused and nothing is stored
    When a GitHub run asks for a gate with <token>
    Then the answer is <status> refusing "<reason>"
    And no request is stored and the journal holds 0 events

    Examples:
      | token                          | status | reason         |
      | a token for another audience   | 401    | wrong-audience |
      | a token of another owner       | 403    | wrong-owner    |
      | a token of another's repository | 403   | wrong-owner    |
      | a token from another branch    | 403    | not-main       |
      | an expired token               | 401    | expired        |
      | a token signed by another key  | 401    | bad-signature  |
      | a token of an unknown key      | 401    | unknown-key    |
      | a token of another issuer      | 401    | bad-token      |
      | a token not yet valid          | 401    | not-yet-valid  |
      | a token with another algorithm | 401    | not-a-token    |
      | a token in two parts           | 401    | not-a-token    |
      | a token that is not a JWT      | 401    | bad-token      |
      | no token                       | 401    | no-token       |

  Scenario: GitHub's keys are fetched once and again for a new key
    When a GitHub run asks for a gate twice, then with a key GitHub has just published
    Then GitHub's keys were fetched 2 times

  Scenario: GitHub's keys that cannot be fetched let no one in
    Given GitHub's keys cannot be fetched
    When a GitHub run asks for a gate with the plan "x"
    Then the answer is 503 refusing "keys-unavailable"

  Scenario: GitHub's keys come through the engine's call log
    Then the keys are fetched through the caller, and a refusal lets no one in

  Scenario Outline: A request that is not well formed is refused
    When a GitHub run posts <body>
    Then the answer is <status> refusing "<reason>"

    Examples:
      | body                            | status | reason          |
      | a request of another kind       | 400    | bad-kind        |
      | a gate without a plan           | 400    | no-plan         |
      | a gate whose plan is over 8 KB  | 400    | plan-too-large  |
      | a body that is not JSON         | 400    | not-json        |
      | a body over 16 KB               | 413    | too-large       |
      | a fulfil of another repository  | 403    | other-repository |
      | a fulfil of a unit not there    | 409    | no-such-unit    |

  Scenario: A listed ServiceAccount may ask for a gate
    When the ServiceAccount integrations:app asks for a gate with the plan "Load the approvals"
    Then the answer is 201 and the request is "awaiting"
    And the cluster was asked to review the token for this site's audience

  Scenario Outline: A ServiceAccount that does not count is refused
    When <account> asks for <kind>
    Then the answer is <status> refusing "<reason>"

    Examples:
      | account                                  | kind     | status | reason             |
      | the ServiceAccount integrations:app      | a fulfil | 403    | gate-only          |
      | the ServiceAccount notifier:app          | a gate   | 403    | not-a-requester    |
      | a token the cluster does not know        | a gate   | 401    | bad-token          |
      | a token of a user, not a ServiceAccount  | a gate   | 403    | not-a-requester    |
      | a token while the cluster is down        | a gate   | 503    | review-unavailable |

  Scenario: A token of neither GitHub's nor the cluster's issuer is refused without asking the cluster
    When a GitHub run asks for a gate with a token of another issuer
    Then the answer is 401 refusing "bad-token"
    And the cluster was not asked

  Scenario: Without a cluster only GitHub's tokens count
    Given the engine has no TokenReview
    When the ServiceAccount integrations:app asks for a gate with the plan "x"
    Then the answer is 401 refusing "bad-token"

  Scenario: Without callers no request is taken
    Given the engine takes no callers
    When a GitHub run asks for a gate with the plan "x"
    Then the answer is 503 refusing "no-callers"

  Scenario: A gate's caller polls until a person approves it
    Given a GitHub run asked for a gate with the plan "<b>Deploy</b> notifier"
    When the person opens the request's page
    Then the page shows the plan escaped and the buttons
    When the person approves it
    Then the run's poll says "approved"
    And the poll answer holds no email
    And the journal holds 2 events, "awaiting", "approved"
    And nothing was walked for it
    And the approved gate has left the open requests, in the next journal event too

  Scenario: Another identity's poll is refused
    Given a GitHub run asked for a gate with the plan "x"
    When another run of the repository polls it
    Then the answer is 403 refusing "not-your-request"
    When the ServiceAccount integrations:app polls it
    Then the answer is 403 refusing "not-your-request"
    When the run polls a request that is not there
    Then the answer is 403 refusing "not-your-request"

  Scenario: A ServiceAccount polls its own gate
    Given the ServiceAccount integrations:app asked for a gate with the plan "x"
    When the ServiceAccount integrations:app polls it
    Then the answer is 200 and the request is "awaiting"

  Scenario: A fulfil's plan is walked read-only and approval runs the walk as the approver
    Given a GitHub run asked for a fulfil of the unit "notifier"
    When the request's plan has been walked
    Then the request is "awaiting" with a plan of "k8s.secret:s" missing
    And the run's poll lists the plan's need "k8s.secret:s" as "missing"
    And the plan walk ran "check --unit notifier" as the run's repository
    When the person approves it and the walk makes it ready
    Then the request ends "done" with "k8s.secret:s" ready
    And the Fulfil walk ran with PUBRULES_WALK_BY the person
    And the journal holds 5 events, "planning", "awaiting", "approved", "running", "done"
    And every event names the request, its plan's sha256, and no value
    And the run's poll says "done" with "k8s.secret:s" ready
    And the poll answer holds no email

  Scenario: A plan that changed since the approval fails and fulfils nothing
    Given a GitHub run asked for a fulfil of the unit "notifier"
    And the request's plan has been walked
    When the need drifts before the person approves it
    Then the request ends "failed", saying "plan changed since approval"
    And no Fulfil walk ran
    And the request's page says "It did not finish: plan changed since approval"

  Scenario: A plan says which needs are not the fulfil's
    Given the engine has walked Sustenaut/notifier and found "k8s.secret:s" missing, "github.secret:SLACK" blocked
    When a GitHub run asks for a fulfil of the unit "notifier"
    And the request's plan has been walked
    Then the plan says "github.secret:SLACK" is "not the fulfil's: github.secret:SLACK is blocked"

  Scenario: A unit with nothing to do is done at once without an approval
    Given the engine has walked Sustenaut/notifier and found "k8s.secret:s" ready
    When a GitHub run asks for a fulfil of the unit "notifier"
    And the request's plan has been walked
    Then the request ends "done" without an approval
    And the journal holds 2 events, "planning", "done"

  Scenario: A plan walk that prints no journal fails the request
    Given the walk of Sustenaut/notifier prints no journal
    When a GitHub run asks for a fulfil of the unit "notifier"
    And the request's plan has been walked
    Then the request ends "failed", saying "printed no journal"

  Scenario: A plan walk without the unit fails the request
    Given the walk of Sustenaut/notifier prints another unit's journal
    When a GitHub run asks for a fulfil of the unit "notifier"
    And the request's plan has been walked
    Then the request ends "failed", saying "no journal for that unit"

  Scenario: A Fulfil walk that fails ends failed with what it said
    Given a GitHub run asked for a fulfil of the unit "notifier"
    And the request's plan has been walked
    When the person approves it and the Fulfil walk fails
    Then the request ends "failed", saying "printed no journal"

  Scenario: A Fulfil walk that cannot start ends failed
    Given a GitHub run asked for a fulfil of the unit "notifier"
    And the request's plan has been walked
    When the person approves it and the clone fails before the Fulfil
    Then the request ends "failed", saying "could not start"

  Scenario: A Fulfil that waits too long for the service ends failed
    Given a GitHub run asked for a fulfil of the unit "notifier"
    And the request's plan has been walked
    When the person approves it while a walk of the service keeps running
    Then the request ends "failed", saying "kept running"

  Scenario: A declined request runs nothing
    Given a GitHub run asked for a fulfil of the unit "notifier"
    And the request's plan has been walked
    When the person declines it
    Then the request ends "declined"
    And no Fulfil walk ran
    And a second decision is refused, saying "not awaiting"

  Scenario: An expired request runs nothing
    Given a GitHub run asked for a fulfil of the unit "notifier"
    And the request's plan has been walked
    When 25 hours pass
    Then the request ends "expired"
    And no Fulfil walk ran
    And the journal's last event is "expired" by "expiry"

  Scenario: An expiry the journal refuses is tried again
    Given a GitHub run asked for a gate with the plan "x"
    And storage refuses the next write
    When 25 hours pass
    Then the request is "awaiting", and the log says the expiry was refused
    When the approvals are listed
    Then the request ends "expired"

  Scenario: A request whose first event the journal refuses is not taken
    Given storage refuses every write
    When a GitHub run asks for a gate with the plan "x"
    Then the answer is 503 refusing "journal-refused"
    And no request is stored and the journal holds 0 events

  Scenario: An approval the journal refuses does not happen
    Given a GitHub run asked for a gate with the plan "x"
    And storage refuses every write
    When the person approves it
    Then the page answers 409 saying "the journal refused it"
    And the request is "awaiting"

  Scenario: A plan the journal refuses ends the request
    Given a GitHub run asked for a fulfil of the unit "notifier"
    And storage refuses the next write
    When the request's plan has been walked
    Then the request ends "failed", saying "the journal refused its plan"
    And no Fulfil walk ran

  Scenario: A start the journal refuses runs nothing
    Given a GitHub run asked for a fulfil of the unit "notifier"
    And the request's plan has been walked
    And storage refuses the next write after the approval
    When the person approves it and the walk makes it ready
    Then the request ends "failed", saying "nothing was run"
    And no Fulfil walk ran

  Scenario: An end the journal refuses is still the end, said in the log
    Given a GitHub run asked for a fulfil of the unit "notifier"
    And storage refuses every write
    When the request's plan has been walked
    Then the request ends "failed", saying "the journal refused"
    And the log says the journal refused its end

  Scenario: A new pod picks up the open requests from the head
    Given a GitHub run asked for a gate with the plan "x"
    And a GitHub run asked for a fulfil of the unit "notifier"
    And the request's plan has been walked
    And the person approved the fulfil, and the engine stopped before running it
    And a third request was planning when the engine stopped
    When a new pod starts the approvals from the head
    Then the gate is still "awaiting" and can be approved
    And the approved fulfil runs, and the planning one ended "failed"

  Scenario: A new pod with no head starts empty, and one storage refuses does not start
    Then a new head starts empty, and a refused head stops the start

  Scenario: The approvals page lists open requests first, then the ended ones
    Given a GitHub run asked for a gate with the plan "x"
    And the ServiceAccount integrations:app asked for a gate with the plan "y"
    And a GitHub run asked for a fulfil of the unit "notifier"
    And the request's plan has been walked
    And the person declined the fulfil
    When the person opens "/approvals"
    Then the page lists 2 open requests and 1 ended
    And the front page says 2 awaiting

  Scenario: The request page links to the run and reloads while it works
    Given a GitHub run asked for a fulfil of the unit "notifier"
    When the person opens the request's page while its plan is walked
    Then the page reloads itself and links to the run
    And a request that is not there is 404

  Scenario Outline: Approve and Decline are for a signed in person with the role, on this site, with their form
    Given a GitHub run asked for a gate with the plan "x"
    When <asker> posts <decision> with <form>
    Then the page answers <status>
    And the request is "awaiting"

    Examples:
      | asker                         | decision | form                           | status |
      | no one signed in              | approve  | the person's form              | 403    |
      | the person without the role   | approve  | the person's form              | 403    |
      | the person, from another site | approve  | the person's form              | 403    |
      | another person                | approve  | the person's form              | 403    |
      | the person                    | approve  | a form for another plan        | 403    |
      | the person                    | maybe    | the person's form              | 400    |
      | the person                    | approve  | a form for a request not there | 404    |

  Scenario: The page shows no buttons to a person without the role
    Given a GitHub run asked for a gate with the plan "x"
    When a person without the role opens the request's page
    Then Approve and Decline are shown disabled, saying "Sign in with the role convergence to decide"

  Scenario: A press's request shows no Approve while its own approval is on its way
    Given the person's Fulfil of the unit "notifier" has its plan and awaits its own approval
    When the person opens the request's page
    Then Approve and Decline are shown disabled, saying "This is someone@example.org's Fulfil"
    And the page reloads itself

  Scenario: A second Approve by the same person is their decision, not an error
    Given a GitHub run asked for a gate with the plan "x"
    When the person approves it twice with the same form
    Then the second answer sends them to the request's page
    And the journal holds 2 events, "awaiting", "approved"

  Scenario: An Approve after another person's decision says what happened
    Given a GitHub run asked for a gate with the plan "x"
    When another person declines it, and then the person approves it with the form they had
    Then the page answers 409 saying "that request is declined (other@example.org"

  Scenario: A press whose own approval the journal refuses awaits a decision like any request
    When the person presses Fulfil and the journal refuses its approval
    Then the request awaits a decision with the buttons, and the log says why

  Scenario Outline: Approve and Decline are shown disabled, saying why, once a request no longer awaits
    Given a GitHub run asked for a fulfil of the unit "notifier"
    When <moment>
    Then Approve and Decline are shown disabled, saying "<why>"

    Examples:
      | moment                                                  | why                                         |
      | the person opens the request's page while its plan is walked | The plan is being walked                |
      | the plan has been walked and the person declined it     | This request is declined: it no longer awaits a decision |

  Scenario: A plan with only what a person must mend ends failed at once, asking no one
    Given the engine has walked Sustenaut/notifier and found "k8s.secrets-declared:notifier" drift graded none detailing "Secret old-key is in the cluster, and nothing reads or declares it", "github.gate:notifier" unknown graded auto
    When a GitHub run asks for a fulfil of the unit "notifier"
    And the request's plan has been walked
    Then the request ends "failed", saying "nothing the engine can fulfil; needs a person: k8s.secrets-declared:notifier (Secret old-key is in the cluster, and nothing reads or declares it"
    And the service page shows "Secret old-key is in the cluster, and nothing reads or declares it" beside the need
    And the request never awaited an approval
    And no Fulfil walk ran
    And the journal holds 2 events, "planning", "failed"
    And the journal's last event gives the reason and the needs "k8s.secrets-declared:notifier", "github.gate:notifier"
    And the run's poll says "failed", giving the reason "nothing the engine can fulfil; needs a person"

  Scenario: A plan with only what the engine cannot judge from where it walks ends done, noted
    Given the engine has walked Sustenaut/notifier and found "k8s.manifests:k8s/admin/network-policy.yaml" unknown graded auto, "github.gate:notifier" unknown graded auto
    When a GitHub run asks for a fulfil of the unit "notifier"
    And the request's plan has been walked
    Then the request ends "done" with the note "nothing the engine can fulfil; unknown from where the engine walks: k8s.manifests:k8s/admin/network-policy.yaml"
    And the request never awaited an approval
    And no Fulfil walk ran
    And the run's poll says "done", giving the note "unknown from where the engine walks"
    And the request's page says "unknown from where the engine walks"

  Scenario: A plan with something the fulfil changes still awaits a person, whatever else it holds
    Given the engine has walked Sustenaut/notifier and found "k8s.secret:s" missing graded auto, "k8s.secrets-declared:notifier" drift graded none
    When a GitHub run asks for a fulfil of the unit "notifier"
    And the request's plan has been walked
    Then the request is "awaiting"
