---
name: RunTests
description: Runs the test suite, measures coverage, and updates the authoritative TDD phase ledger from what it observes.
---
Runs pytest in the workspace and returns the full output.

Tests failing is a result, not an error. The output includes the exit code, a line saying
what the run did to the TDD ledger and what the phase now needs, the line and branch
coverage report with the uncovered lines (`Missing`), and is verbose on purpose because
the failure detail is the most valuable thing you will read.

Updates the TDD phase ledger from what it observes: in RED, a run of the sub-requirement's
new or changed tests (pass or fail) ends RED; in GREEN, a pass ends GREEN; in REFACTOR, a
pass with enough coverage ends REFACTOR. Only a pass (0) or failing tests (1) count;
anything else (no tests collected, usage error, timeout) is reported as an error.
