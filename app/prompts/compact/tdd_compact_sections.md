---
name: compact-tdd-sections
description: The TDD sections appended to the compaction summary request.
authored-by: TDDAgents
provenance: >-
  TDDAgents addition (Phase 5): the TDD sections the summary must carry. The
  authoritative TDD state is still re-attached after the boundary from the ledger and
  the last RunTests result, never reconstructed from this summary.
---
## TDD state

This conversation is one test-driven development (TDD) cycle. In addition to the sections above, your summary must include the following TDD sections. They are for continuing the work; the authoritative TDD state is re-attached after your summary from the phase ledger and the last RunTests result, and wherever the two disagree the re-attached state wins.

10. Phase ledger: The TDD phase (RED, GREEN or REFACTOR), whether the sub-requirement's tests were written and run (tests_written), whether they passed in GREEN (green_passed), whether REFACTOR passed (refactor_passed), and the last measured coverage, exactly as the most recent RunTests result reported them.
11. Latest RunTests evidence: The most recent RunTests output that matters for the next step — a failing test's name, assertion or error, file and line, or the uncovered lines of the coverage report — quoted verbatim.
12. Test files: Every test file written or edited in this cycle, with its path.
13. Sub-requirement: The sub-requirement this cycle implements, quoted verbatim from the user's request.
