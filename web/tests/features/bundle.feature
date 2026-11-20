Feature: The built bundle, as a browser runs it
  /static/run.js is the bundle esbuild builds (make web, and the image's
  node stage). These scenarios run that file, not the sources, in a fresh
  document holding the page's data block, as a browser would.

  Scenario: The bundle draws Start a run
    Given the built bundle
    When it runs on a page whose data block is a Start a run form for "Sustenaut/integrations"
    Then it raised no error
    And the page shows the heading "Start a run" and the button "Start and plan"

  Scenario: The bundle draws a run awaiting approval
    Given the built bundle
    When it runs on a page whose data block is a run awaiting approval
    Then it raised no error
    And the page shows the heading "integrations" and the button "Stop"
