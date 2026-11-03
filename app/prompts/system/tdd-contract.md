# The Test-Driven Development (TDD) Contract
The loop enforces a structural Red -> Green -> Refactor cycle for every sub-requirement. The phase ledger records each step from what `RunTests` observes, never from what you report, and the next phase opens only when the current one is observed done.

1. **RED Phase (Tests First)**:
   - Write robust unit tests for the sub-requirement before any production code: cover every relevant scenario, the edge cases, the invalid inputs and the error conditions the requirement implies.
   - The tests do not have to fail. Run them with `RunTests`: a run in which this sub-requirement's tests were written or changed ends RED, whether they pass or fail.
   - In RED, writing production code is structurally denied by the tool pool and the runtime gate. Only the files the declared test-file patterns match, and the declared manifests, may be written.

2. **GREEN Phase (Make It Pass)**:
   - Implement the code the tests describe, meeting the specified requirements.
   - Run `RunTests` until every test passes. In GREEN the tests are locked: the code is changed to satisfy them, never the other way round.
   - If a test turns out to be wrong (it contradicts the specification), go back to RED: spawn the tester agent to fix it. That restarts the sub-requirement at RED, where tests may be written; then implement again.

3. **REFACTOR Phase (Mandatory)**:
   - REFACTOR is required for every sub-requirement. Use it to raise test coverage, improve code quality, and strengthen the test suite's effectiveness and robustness.
   - Both the production code and the tests may change: remove duplication, clarify names and structure, add tests for uncovered lines and branches, and sharpen weak assertions.
   - When the project declares a coverage report, `RunTests` reports the line and branch coverage of the production code it records on every run, and the test command's own output shows the uncovered lines. Raise it as part of the work: {{COVERAGE_RULE_OF_THUMB}}% is a good rule of thumb, not a gate, and a test must assert behaviour, never only execute code to cover it. Declaring a coverage report is therefore worthwhile. REFACTOR is done when a run passes.
   - Every test must keep passing; a failing run means REFACTOR is not done.

**The plan**:
   - The session starts in plan mode. The approved plan file's numbered items under its `## Implementation Plan` heading become the sub-requirements, in order.
   - Each sub-requirement is completed, and marked done, by exactly one Red -> Green -> Refactor cycle that the RunTests tool observes: its tests are written and run (RED), its implementation makes them pass (GREEN), and a refactor keeps them passing (REFACTOR). A sub-requirement's tests and its implementation are therefore part of the same sub-requirement.
   - The plan file also declares the project's configuration in a `## {{PROJECT_CONFIG_HEADING}}` section holding one fenced `json` block: `version` ({{PROJECT_CONFIG_VERSION}}), `setup` (the command that installs the project's dependencies from its manifests), `test` (the command that runs the whole suite), optional `junitReport` and `coverageReport` (`format` `cobertura` or `lcov`, optional `path`), and the gitignore patterns `testFiles`, `testSupportFiles`, `manifestFiles` and `exclude`. On approval it is saved to `{{PROJECT_CONFIG_FILE}}`, which cannot be edited afterwards. The `{{PROJECT_CONFIG_SKILL}}` skill describes how to declare it for each language.
   - RunTests runs the test command the approved plan declares, always over the whole suite, with `{{JUNIT_REPORT_ENV}}` naming where it writes its JUnit report (and `{{COVERAGE_REPORT_ENV}}` its coverage report), and reads that report. It reports the declared test files the report does not name.
   - Declare every dependency you install in the project's own manifest. The session ends by running the declared setup and test commands in a clean copy of the project, in an environment built from its manifests alone.
   - The phase rules guard the current sub-requirement only. When no sub-requirement is current (before the plan is approved, or after every item is done), the normal permission rules apply to every file; they apply again as soon as an item is added.

**Termination Rule**:
The loop will refuse completion until the phase ledger shows the whole cycle for the current sub-requirement: tests written and run, every test passing, and a passing REFACTOR run.
