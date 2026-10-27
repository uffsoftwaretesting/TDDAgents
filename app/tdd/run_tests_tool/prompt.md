---
name: RunTests
description: Runs the test suite and updates the authoritative TDD phase ledger from its exit code.
---
Runs pytest in the workspace and returns the full output.

Tests failing is a result, not an error — in TDD a failing test is frequently the point.
The output includes the exit code, and is verbose on purpose because the failure detail
is the most valuable thing you will read.

Updates the TDD phase ledger from the observed exit code: only a pass (0) or failing tests (1)
change it; anything else (no tests collected, usage error, timeout) is reported as an error.
