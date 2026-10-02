---
name: RunTests
description: Runs the project test suite and updates the authoritative TDD phase ledger.
---
Executes the project's test runner (e.g., pytest) and records ground truth test outcomes.

Usage:
- Optionally provide `test_target` (test path or test name pattern) to run a focused subset.
- Automatically updates the authoritative `AppState.phase_ledger` with exit code, failure counts, and test passes.
- This is the ground truth for confirming RED and verifying GREEN across the TDD cycle.
- Always check the actual test output before drawing conclusions. Report outcomes faithfully: if tests fail, say so with the output.
