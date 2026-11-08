---
name: RunTests
description: Runs the project's declared test command over the whole suite, reads its JUnit and coverage reports, and records the run in the TDD phase ledger.
---
Runs the test command the approved plan declares for the project, over the whole suite, and returns what it observed:

- the command, and its exit code with the shell's meaning of it;
- what the JUnit report the command wrote records: the number of tests, and how many failed, errored or were skipped;
- the project's listTests command and its exit code, when the JUnit report names no test files and the project declares that command;
- the declared test files that did not run, the files that ran which the declaration does not cover, or that which test files ran is unknown;
- the line and branch coverage of the production code, when the project declares a coverage report;
- a `TDD ledger:` line: what the run did to the TDD ledger, the current phase's rule, and whether the run met it;
- the raw output of the command. It is verbose on purpose: the failure detail is the most valuable thing you will read.

Failing tests are a result, not an error.

The phase rules:
- RED ends with a completed run of the test command (its tests pass or fail), in which no declared test file is named as not run and no file the declaration does not cover ran, after the sub-requirement's test files changed.
- GREEN and REFACTOR each end with a run that exits 0, whose JUnit report records tests, none failed, errored or skipped, and in which every declared test file is named as run (by the JUnit report, else by the listTests command) and no file the declaration does not cover ran.

Any other run (a command that is not executable or not found, one that was killed or timed out, an exit 0 with no JUnit report or no tests in it) is reported as an error and leaves the ledger unchanged.
