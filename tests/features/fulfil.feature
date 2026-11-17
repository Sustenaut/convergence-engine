Feature: A person's Fulfil
  The write token reaches that walk alone, never a round's. A press of the button is
  a request its person approves at once, once its plan is walked.

  Scenario: The rounds token may only read and is reused
    Then the rounds token may only read and is reused

  Scenario: A fulfils token has the installations permissions and is minted each time
    Then a fulfils token has the installations permissions and is minted each time

  Scenario: A refused mint raises
    Then a refused mint raises

  Scenario: A rounds walk holds no write token
    Given the engine has walked Sustenaut/notifier
    Then a rounds walk holds no write token

  Scenario: A press walks the plan, then one unit with the write tokens as the person
    Given the engine has walked Sustenaut/notifier
    When the person presses Fulfil for the unit "notifier" and the walk makes it ready
    Then the request ends "done"
    And the plan walk was read-only and the Fulfil walk had the write tokens as the person
    And the service page shows the need ready

  Scenario: A fulfil that prints no journal says why
    Given the engine has walked Sustenaut/notifier
    When the person presses Fulfil for the unit "notifier" and the walk prints no journal
    Then the request ends "failed", saying "printed no journal"

  Scenario: A fulfil whose clone fails says why and frees the service
    Given the engine has walked Sustenaut/notifier
    And the clone of Sustenaut/notifier fails
    When the person presses Fulfil for the unit "notifier"
    Then the request ends "failed", saying "the plan walk could not start"
    And the service can be walked again

  Scenario: A fulfil waits for a walk of that service, and ends if it keeps running
    Given the engine has walked Sustenaut/notifier
    And a walk of Sustenaut/notifier is running
    When the person presses Fulfil for the unit "notifier"
    Then the request ends "failed", saying "kept running"

  Scenario Outline: A fulfil of what is not there is refused
    Given the engine has walked Sustenaut/notifier
    When the person presses Fulfil for the unit "<unit>" of "<repository>"
    Then the press is refused, saying "<said>"

    Examples:
      | repository         | unit | said            |
      | Sustenaut/nothing  | x    | not been walked |
      | Sustenaut/notifier | x    | no such unit    |

  Scenario: An engine without a write token fulfils nothing
    Given the engine without a write token has walked Sustenaut/notifier
    When the person presses Fulfil for the unit "notifier" of "Sustenaut/notifier"
    Then the press is refused, saying "fulfils nothing"

  Scenario: The page offers a fulfil to the signed in person with the role, at the top and on the unit
    Given the engine has walked Sustenaut/notifier
    When the person opens the service page
    Then Fulfil notifier can be pressed, at the top and on the unit

  Scenario: The role counts in any of oauth2-proxy's group lines
    Given the engine has walked Sustenaut/notifier
    When the person, with the role on the second of two group lines opens the service page
    Then Fulfil notifier can be pressed, at the top and on the unit

  Scenario Outline: Fulfil is shown disabled, saying why, when it cannot be pressed
    Given the engine has walked Sustenaut/notifier
    And <situation>
    When <viewer> opens the service page
    Then Fulfil notifier is shown disabled, at the top and on the unit, saying "<why>"

    Examples:
      | situation                                   | viewer                      | why                                                  |
      | nothing more                                | no one signed in            | Sign in with the role convergence to fulfil          |
      | nothing more                                | the person without the role | Sign in with the role convergence to fulfil          |
      | every need is ready                         | the person                  | Nothing to fulfil: every need is ready               |
      | the need left is a proposal                 | the person                  | Nothing the engine can fulfil                        |
      | a request to fulfil the unit is open        | the person                  | A request to fulfil this unit is already open        |
      | a walk of Sustenaut/notifier is running now | the person                  | A walk of this service is running                    |

  Scenario: An engine without a write token shows Fulfil disabled
    Given the engine without a write token has walked Sustenaut/notifier
    When the person opens the service page
    Then Fulfil notifier is shown disabled, at the top and on the unit, saying "no installation token that may write"

  Scenario: A Fulfil posted while a request for the unit is open is refused, saying why
    Given the engine has walked Sustenaut/notifier
    And a request to fulfil the unit is open
    When the person posts the Fulfil form they had
    Then the page answers 409 saying "already open"

  Scenario: A fulfil from the page runs and its request page says how it went
    Given the engine has walked Sustenaut/notifier
    When the person posts the Fulfil form and the walk makes it ready
    Then the answer sends them to the request's page
    And the request's page says it is done, approved by the person
    And the old Fulfil link leads to the request's page

  Scenario: A fulfil the engine refuses is said
    Given the engine has walked Sustenaut/notifier
    When the person posts the Fulfil form for the unit "x"
    Then the page answers 409 saying "no such unit"

  Scenario: A press the journal refuses starts nothing
    Given the engine has walked Sustenaut/notifier
    And storage refuses every write
    When the person posts the Fulfil form and the walk makes it ready
    Then the page answers 409 saying "the journal refused it"
    And no Fulfil walk ran

  Scenario: A round gets the read paths google credential alone
    Given the engine with a Google pool has walked Sustenaut/notifier
    Then a round gets the read paths google credential alone

  Scenario: A fulfil gets the write paths google credential and its own gcloud directory
    Given the engine with a Google pool has walked Sustenaut/notifier
    When the person presses Fulfil for the unit "notifier" and the walk makes it ready
    Then the fulfil got the write paths google credential and its own gcloud directory

  Scenario: Without a pool no walk gets a google credential
    Given the engine has walked Sustenaut/notifier
    Then no walk gets a google credential

  Scenario Outline: A fulfil without the person, the role, this site or their form is refused
    Given the engine has walked Sustenaut/notifier
    When <asker> posts a fulfil with <form>
    Then the page answers 403

    Examples:
      | asker                          | form                      |
      | no one signed in               | their form                |
      | the person without the role    | their form                |
      | the person, from another site  | their form                |
      | the person                     | a form with another token |
      | another person                 | their form                |
