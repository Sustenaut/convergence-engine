Feature: The live run page in a browser
  The bundle draws the run from what the engine embedded, polls for the
  journal after the last event it holds, and posts each action. These
  scenarios draw it in jsdom against an engine faked at fetch.

  Scenario: A switch change posts the mode and moves plan_key, and holds Approve back for 1.5 s
    Given a run awaiting approval, with a need to fulfil and a need for a person
    And the engine answers a mode switch with the plan_key "c9f1"
    When the page is drawn
    Then the plan_key reads "b7e2"
    And the plan reads "2 needs · 1 to fulfil · 0 check only · 1 needs a person"
    When the person switches "ingest-token" to "Check"
    Then the page told the engine "POST" "/runs/r1/needs/ingest-token/mode" with '{"mode":"check"}'
    And the plan_key reads "c9f1"
    And the plan reads "2 needs · 0 to fulfil · 1 check only · 1 needs a person"
    And the button "Approve 0 fulfils" is disabled
    When the settle time passes
    Then the button "Approve 0 fulfils" is enabled

  Scenario: A need for a person has no switch, and its cell keeps its place
    Given a run awaiting approval, with a need to fulfil and a need for a person
    When the page is drawn
    Then "namespace" has no switch, only "—"
    And "ingest-token"'s switch can be changed

  Scenario: Approve is disabled for a viewer who may not act
    Given a run awaiting approval, with a need to fulfil and a need for a person
    And a viewer who may not act
    When the page is drawn
    Then the button "Approve 1 fulfil" is disabled
    And the button "Stop" is disabled
    And "ingest-token"'s switch is frozen

  Scenario: Approve posts the plan_key and freezes the switches
    Given a run awaiting approval, with a need to fulfil and a need for a person
    And the engine answers Approve with the run running
    When the page is drawn
    And the person presses "Approve 1 fulfil"
    Then the page told the engine "POST" "/runs/r1/approve" with '{"plan_key":"b7e2"}'
    And "ingest-token"'s switch is frozen
    And the journal's last line reads "09:03:00 · run · awaiting → running · you"

  Scenario: Stop on a mixed run: one need runs to its end, one finishes early, one never started
    Given a running fulfil a person stopped: a need that continues, one that shortens, and one stopped
    When the page is drawn
    Then the header says "Stopping · 1 need runs to its end"
    And the header says "database runs to its end unless you cancel it below"
    And the button "Stop pressed" is disabled
    And the row of "database" reads "Runs to its end"
    And the row of "database" offers to cancel it, warning "Cancelling a database operation partway can leave a half-applied migration."
    And the row of "deploy" reads "finishing"
    And the row of "deploy" offers no cancel
    And the row of "secrets-declared" reads "stopped"

  Scenario: Cancelling a need that runs to its end needs its name typed
    Given a running fulfil a person stopped: a need that continues, one that shortens, and one stopped
    When the page is drawn
    And the person types "datab" to cancel "database"
    Then the button "Cancel database" is disabled
    When the person types "database" to cancel "database"
    And the person presses "Cancel database"
    Then the page told the engine "POST" "/runs/r1/needs/database/cancel" with '{"confirm":"database"}'

  Scenario: The page polls for the journal after the last event it holds, and recomputes time in state
    Given a run awaiting approval, with a need to fulfil and a need for a person
    And the engine has one new event after event 2
    When the page is drawn
    Then the row of "ingest-token" reads "1 min 30 s"
    When a minute passes
    And 100 ms pass
    Then the page polled the run after event 2
    And the journal's last line reads "09:03:00 · ingest-token · waiting → checking · engine"
    And the row of "ingest-token" reads "2 min 30 s"

  Scenario: Start a run explains a deploy unit, and labels what the run does beside the dropdowns
    Given the engine lists the repository "Sustenaut/integrations" with the deploy unit "integrations"
    When Start a run is drawn
    Then the field "Deploy unit" is described by a line saying "A deploy unit is one part of a repository"
    And the page shows the label "What the run does" over the Check and Fulfil switch

  Scenario: Start a run with no repository listed says why
    Given the engine lists no repository
    When Start a run is drawn
    Then the page says "The engine lists no repository yet" and links to the overview
