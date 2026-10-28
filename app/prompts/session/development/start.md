Implement this project test-first, one sub-requirement at a time, following the TDD plan below.

For each sub-requirement:

1. RED: write robust unit tests for it, covering every relevant scenario and edge case, and run RunTests. The tests do not have to fail; a run of this sub-requirement's new or changed tests ends RED.
2. GREEN: implement the code the tests describe and run RunTests until every test passes. The tests are locked in GREEN; if one is wrong, spawn the tester agent, which restarts the sub-requirement at RED so it can be fixed.
3. REFACTOR (mandatory): improve the code and the tests and raise line and branch coverage of the production code ({{COVERAGE_RULE_OF_THUMB}}% is a good rule of thumb), then run RunTests until every test passes. RunTests lists the uncovered lines.

When a sub-requirement's cycle has been observed, the system marks it done and gives you the next one; you cannot mark one done yourself. Work on the current sub-requirement only: its RED needs tests that are new or changed for it.

The user may interrupt at any time to discuss the work or to change the plan. Use UpdatePlan to change sub-requirements, never to skip one.

## Technical specification

{{SPECIFICATION}}

## TDD plan

{{PLAN}}

Start with sub-requirement {{CURRENT}}.
