Feature: Clones and the walk's subprocess

  Each repository is a shallow clone of its default branch, refreshed before each
  walk. The installation token reaches git only in its environment, and the walk's
  subprocess gets an environment built from nothing.

  Scenario: A clone is made once, then follows the default branch
    Given a scratch directory
    And a git repository
    And a file ".gitignore" holding "work/"
    And a file "pubrules.json" holding "one"
    And everything is committed with the message "one"
    And a bare remote "Sustenaut/svc"
    And the engine clones from the bare remotes
    And the secret "ghs_secret_installation"
    When the engine refreshes its clone of Sustenaut/svc
    Then the file "work/clones/Sustenaut/svc/pubrules.json" holds "one"
    When the file "pubrules.json" changes to:
      """
      two
      """
    And the change is pushed to "Sustenaut/svc"
    And the file "work/clones/Sustenaut/svc/stray" changes to:
      """
      x
      """
    And the engine refreshes its clone of Sustenaut/svc
    Then the file "work/clones/Sustenaut/svc/pubrules.json" holds "two"
    And the file "work/clones/Sustenaut/svc/stray" does not exist
    And no secret appears in what it produced

  Scenario: git's environment carries the token as a header, and asks nobody
    When git's environment is made for the token "ghs_x"
    Then the result's "GIT_CONFIG_KEY_0" is "http.https://github.com/.extraheader"
    And the result's "GIT_CONFIG_VALUE_0" is "Authorization: Basic eC1hY2Nlc3MtdG9rZW46Z2hzX3g="
    And the result's "GIT_TERMINAL_PROMPT" is "0"

  Scenario Outline: A command that cannot finish says why
    When the engine runs "<command>" for at most 1 second
    Then the result is:
      """
      {"code": <code>, "stdout": "", "stderr": "<said>"}
      """

    Examples:
      | command          | code | said                         |
      | sleep 5          | 124  | sleep ran past 1 s           |
      | no-such-program  | 127  | no-such-program did not run (FileNotFoundError) |
      | sh -c "exit 3"   | 3    |                              |

  Scenario: A git that fails without a word still says its exit
    Given git fails silently with 5
    When the engine refreshes its clone of Sustenaut/svc
    Then it is refused, saying "Sustenaut/svc: git clone failed: exit 5"

  Scenario: A repository without ./pubdeploy is not walked
    Given a clone of Sustenaut/svc without ./pubdeploy
    When it is walked
    Then the result's "ok" is "false"
    And the result's "error" is "the repository has no ./pubdeploy: its pubrules sync has not run with a deploy block"

  Scenario: Journals that are not deploy-journal/2 are no journals
    Given a clone of Sustenaut/svc whose walk prints '[{"schema": "other"}]'
    When it is walked
    Then the result's "ok" is "false"
    And the result's "error" is "the walk printed no journal (exit 0)"

  Scenario: Each need's state is one row of facts, a need never checked observed at the walk's start
    Given a journal of Sustenaut/svc with "k8s.namespace:svc" ready and "github.secret:X" unknown
    When its facts are made
    Then the result is:
      | need              | state   | observed_at               | walk_started_at           |
      | k8s.namespace:svc | ready   | 2026-11-10 06:00:01+00:00 | 2026-11-10 06:00:00+00:00 |
      | github.secret:X   | unknown | 2026-11-10 06:00:00+00:00 | 2026-11-10 06:00:00+00:00 |

  Scenario: The facts of a walk are a file that reads back as the same rows
    Given a journal of Sustenaut/svc with "k8s.namespace:svc" ready and "github.secret:X" unknown
    When its facts are made into a file
    Then the file decodes to the same rows, and the same rows make the same file

  Scenario: The facts' file has its place by the walk's start, service and contents
    Given a journal of Sustenaut/svc with "k8s.namespace:svc" ready and "github.secret:X" unknown
    When its facts' place is found
    Then the result matches "inbox/need-states/2026-11-10/060000-sustenaut.svc.svc-*.avro"

  Scenario Outline: Head names are flat lower case and short
    When the head of "<repository>" unit "<unit>" is named
    Then the result is "<head>"

    Examples:
      | repository                     | unit                                                                             | head                                                             |
      | Sustenaut/release-controlplane | release-schryer                                                                  | sustenaut.release-controlplane.release-schryer                   |
      | Some_Owner/Repo                | UUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUU | some_owner.repo.uuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuu |
