Feature: A person's Fulfil
  The write token reaches that walk alone, never a round's.

  Scenario: The rounds token may only read and is reused
    Then the rounds token may only read and is reused

  Scenario: A fulfils token has the installations permissions and is minted each time
    Then a fulfils token has the installations permissions and is minted each time

  Scenario: A refused mint raises
    Then a refused mint raises

  Scenario: A rounds walk holds no write token
    Then a rounds walk holds no write token

  Scenario: A fulfil walks one unit with the write tokens as the person
    Then a fulfil walks one unit with the write tokens as the person

  Scenario: A fulfil that prints no journal says why
    Then a fulfil that prints no journal says why

  Scenario: A fulfil that cannot start says why
    Then a fulfil that cannot start says why

  Scenario: No fulfil while that service is walked
    Then no fulfil while that service is walked

  Scenario: An engine without a write token fulfils nothing
    Then an engine without a write token fulfils nothing

  Scenario: The page offers a fulfil to the signed in person alone
    Then the page offers a fulfil to the signed in person alone

  Scenario: A fulfil from the page runs and its page says how it went
    Then a fulfil from the page runs and its page says how it went

  Scenario: A running fulfils page reloads itself
    Then a running fulfils page reloads itself

  Scenario: A fulfil the engine refuses is said
    Then a fulfil the engine refuses is said

  Scenario: A round gets the read paths google credential alone
    Then a round gets the read paths google credential alone

  Scenario: A fulfil gets the write paths google credential and its own gcloud directory
    Then a fulfil gets the write paths google credential and its own gcloud directory

  Scenario: Without a pool no walk gets a google credential
    Then without a pool no walk gets a google credential

  Scenario Outline: A fulfil of what is not there is refused
    When a fulfil of the unit "<unit>" of "<repository>" is asked for
    Then the engine refuses it, saying "<said>"

    Examples:
      | repository         | unit | said            |
      | Sustenaut/nothing  | x    | not been walked |
      | Sustenaut/notifier | x    | no such unit    |

  Scenario Outline: A fulfil without the person, this site or their form is refused
    When <asker> posts a fulfil with <form>
    Then the page answers 403

    Examples:
      | asker                          | form                      |
      | no one signed in               | their form                |
      | the person, from another site  | their form                |
      | the person                     | a form with another token |
      | another person                 | their form                |
