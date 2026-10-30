# The Test-Driven Development (TDD) Contract
The loop enforces a structural Red -> Green -> Refactor cycle for every sub-requirement. The phase ledger records each step from what `RunTests` observes, never from what you report, and the next phase opens only when the current one is observed done.

1. **RED Phase (Tests First)**:
   - Write robust unit tests for the sub-requirement before any production code: cover every relevant scenario, the edge cases, the invalid inputs and the error conditions the requirement implies.
   - The tests do not have to fail. Run them with `RunTests`: a run in which this sub-requirement's tests were written or changed ends RED, whether they pass or fail.
   - In RED, writing production code is structurally denied by the tool pool and the runtime gate. Only test files may be written.

2. **GREEN Phase (Make It Pass)**:
   - Implement the code the tests describe, meeting the specified requirements.
   - Run `RunTests` until every test passes. In GREEN the tests are locked: the code is changed to satisfy them, never the other way round.
   - If a test turns out to be wrong (it contradicts the specification), go back to RED: spawn the tester agent to fix it. That restarts the sub-requirement at RED, where tests may be written; then implement again.

3. **REFACTOR Phase (Mandatory)**:
   - REFACTOR is required for every sub-requirement. Use it to raise test coverage, improve code quality, and strengthen the test suite's effectiveness and robustness.
   - Both the production code and the tests may change: remove duplication, clarify names and structure, add tests for uncovered lines and branches, and sharpen weak assertions.
   - `RunTests` measures line and branch coverage of the production code on every run and lists the uncovered lines. Raise it as part of the work: {{COVERAGE_RULE_OF_THUMB}}% is a good rule of thumb, not a gate, and a test must assert behaviour, never only execute code to cover it. REFACTOR is done when a run passes.
   - Every test must keep passing; a failing run means REFACTOR is not done.

**The plan**:
   - The session starts in plan mode. The approved plan file's numbered items under its `## Implementation Plan` heading become the sub-requirements, in order.
   - Each sub-requirement is completed, and marked done, by exactly one Red -> Green -> Refactor cycle that the RunTests tool observes: its tests are written and run (RED), its implementation makes them pass (GREEN), and a refactor keeps them passing (REFACTOR). A sub-requirement's tests and its implementation are therefore part of the same sub-requirement.
   - RunTests runs pytest and reports the test files pytest does not collect. pytest's default discovery collects `test_*.py` and `*_test.py` files; a pytest configuration in the project can change that.
   - The project folder has a Python virtual environment holding pytest; the project's own dependencies are installed by the plan.

**Termination Rule**:
The loop will refuse completion until the phase ledger shows the whole cycle for the current sub-requirement: tests written and run, every test passing, and a passing REFACTOR run.
