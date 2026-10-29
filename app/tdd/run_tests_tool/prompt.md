---
name: RunTests
description: Runs the test suite with pytest, measures coverage, and records the run in the TDD phase ledger.
---
Runs pytest in the workspace and returns what it observed:

- the exit code with pytest's meaning of it;
- the test files pytest reported collection errors for, the test files it did not collect, and the number of skipped tests, as pytest's reports record them;
- the line and branch coverage it measured;
- a `TDD ledger:` line: what the run did to the TDD ledger, the current phase's rule, and whether the run met it;
- the raw pytest output, which includes the uncovered lines (`Missing`). It is verbose on purpose: the failure detail is the most valuable thing you will read.

Failing tests are a result, not an error.

The phase rules:
- RED ends with a run that passes, fails, or stops on collection errors pytest reports, after the sub-requirement's test files changed.
- GREEN and REFACTOR each end with a run in which every test passes (exit 0), none is skipped and every test file is collected.

Any other run (interrupted, an internal or usage error, no tests collected, a timeout) is reported as an error and leaves the ledger unchanged.
