Feature: Clones and the walk's subprocess

  Each repository is a shallow clone of its default branch, refreshed before each
  walk. The installation token reaches git only in its environment, and the walk's
  subprocess gets an environment built from nothing.

  Scenario: A clone is made once, then follows the default branch
    Given a repository Sustenaut/svc on a local git server, with pubkit.json "one"
    When the engine refreshes its clone of Sustenaut/svc
    Then the clone's pubkit.json is "one"
    When the repository's pubkit.json becomes "two" and a stray file is left in the clone
    And the engine refreshes its clone of Sustenaut/svc
    Then the clone's pubkit.json is "two"
    And the stray file is gone
    And no file in the clone holds the token

  Scenario: git's environment carries the token as a header, and asks nobody
    When git's environment is made for the token "ghs_x"
    Then it sends "Authorization: Basic eC1hY2Nlc3MtdG9rZW46Z2hzX3g=" to github.com
    And it never prompts

  Scenario Outline: A command that cannot finish says why
    When the engine runs "<command>" for at most 1 second
    Then it ended with <code> and said "<said>"

    Examples:
      | command          | code | said                         |
      | sleep 5          | 124  | sleep ran past 1 s           |
      | no-such-program  | 127  | no-such-program did not run (FileNotFoundError) |
      | sh -c "exit 3"   | 3    |                              |

  Scenario: A git that fails without a word still says its exit
    Given git fails silently with 5
    When the engine refreshes its clone of Sustenaut/svc
    Then the refresh fails with "Sustenaut/svc: git clone failed: exit 5"

  Scenario: A repository without ./pubdeploy is not walked
    Given a clone of Sustenaut/svc without ./pubdeploy
    When it is walked
    Then the walk failed with "the repository has no ./pubdeploy: its pubkit sync has not run with a deploy block"

  Scenario: Journals that are not deploy-journal/2 are no journals
    Given a clone of Sustenaut/svc whose walk prints '[{"schema": "other"}]'
    When it is walked
    Then the walk failed with "the walk printed no journal (exit 0)"

  Scenario: Each need's state is one row of facts, a file a walk
    Given a journal of Sustenaut/svc with "k8s.namespace:svc" ready and "github.secret:X" unknown
    When its facts are made
    Then there are 2 rows, and "github.secret:X" is unknown, observed at the walk's start
    And the file decodes to the same rows, and the same rows make the same file
    And its place is "inbox/need-states/2026-11-10/060000-sustenaut.svc.svc-"
