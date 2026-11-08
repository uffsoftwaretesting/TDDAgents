"""
What a run of the project's test command is, as TDD evidence: one pure judgement shared by RunTests
and the session's clean final run (2026-11-03).

**The exit code is read as a shell reads it** (user decision): the declared test command can be any
runner of any language, so no runner's own table applies. Upstream's Bash does the same
(`tools/BashTool/commandSemantics.ts`: an exit code maps to `isError` and a label, never the output):

* 0 — the command completed; a pass if its JUnit report agrees;
* 126 / 127 — the command is not executable / was not found: an error, not evidence;
* 128 and above (or a negative code, a signal) — the command was killed: an error, not evidence; a
  timeout is the same, raised before a code exists;
* any other non-zero code — a failed run, which is evidence. A load or compile error counts as a
  failure, whatever the runner reports.

**`judge` decides from the facts alone** (`exit`, the JUnit report, what the run collected
(`app/tdd/collection_gate.py::Collection`), the phase):

* a pass = exit 0 ∧ a report ∧ tests > 0 ∧ no failure, error or skip ∧ every declared test file run ∧ no
  file run that the declaration does not cover;
* exit 0 with no report, or a report with no tests, is not evidence (an error): nothing shows a test ran;
* RED needs a completed run (the caller adds "after the test files changed");
* the collection is judged on every run that has a report, whatever its exit code (2026-11-08): a
  declared test file that did not run, or an undeclared file that ran, keeps every phase from its rule,
  RED included;
* files that stay unknown (the report names none and no `listTests` listing named them) keep GREEN and
  REFACTOR from a pass (strict collection, user decision 2026-11-08); RED states it and is not held to it
  (`stated`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.tdd.collection_gate import Collection
from app.tdd.junit_report import JUnitReport
from app.tdd.ledger import TddPhase


class Outcome(Enum):
    """What a run is, as evidence. `None` (no outcome) is a run that is not evidence."""

    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExitSemantic:
    """Upstream's `CommandSemantic` shape: what an exit code means and whether it is an error."""

    meaning: str
    is_error: bool
    #: The command ran to its end: a completed run, passing or failing.
    completed: bool


#: The codes with a fixed meaning; every other code is read by `exit_semantic`'s rules.
SHELL_EXIT_SEMANTICS: dict[int, ExitSemantic] = {
    0: ExitSemantic("the test command completed", False, True),
    126: ExitSemantic("the test command is not executable", True, False),
    127: ExitSemantic("the test command was not found", True, False),
}

#: The first exit code a shell gives a command killed by a signal (128 + the signal number).
SIGNAL_EXIT_BASE = 128


def exit_semantic(exit_code: int) -> ExitSemantic:
    """The table's entry, else: killed by a signal (≥ 128, or negative) is an error; any other code a failed run."""
    known = SHELL_EXIT_SEMANTICS.get(exit_code)
    if known is not None:
        return known
    if exit_code >= SIGNAL_EXIT_BASE or exit_code < 0:
        signal = exit_code - SIGNAL_EXIT_BASE if exit_code >= SIGNAL_EXIT_BASE else -exit_code
        return ExitSemantic(f"the test command was killed by signal {signal}", True, False)
    return ExitSemantic("the test command failed", False, True)


#: Why an exit-0 run is not evidence.
NO_REPORT = "the test command wrote no JUnit report"
NO_TESTS = "the JUnit report records no tests"
#: The collection facts that keep every phase from its rule, RED included.
NOT_RUN = "a declared test file did not run"
UNDECLARED = "a file the run ran is not a declared test file"


@dataclass(frozen=True, slots=True)
class Judgement:
    """A run judged: its outcome as evidence, whether it is an error, and the facts that kept it from a pass."""

    exit_code: int
    meaning: str
    #: `None` when the run is not evidence.
    outcome: Outcome | None
    is_error: bool
    #: Why an exit-0 run is not evidence (`NO_REPORT`, `NO_TESTS`), else `None`.
    not_evidence: str | None
    #: The facts that keep the run from a pass, in order; empty for a pass.
    unmet: tuple[str, ...]
    #: Declared test files the run did not run; `None` when that is not observable.
    uncollected: tuple[str, ...] | None
    #: Whether the run meets the phase's rule (for RED: before "the test files changed").
    meets_phase: bool
    #: Files the run ran inside the project that the declaration does not cover.
    undeclared: tuple[str, ...] = ()
    #: Facts stated without holding this phase to them (RED: the files are unknown).
    stated: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.outcome == Outcome.PASSED and not self.unmet


def judge(exit_code: int, report: JUnitReport | None, collection: Collection | None,
          phase: TddPhase) -> Judgement:
    """
    The judgement of one run (see the module docstring). Pure: it reads nothing but its arguments.
    `collection` is `None` when there is nothing to observe it from (no declaration, or no report).
    """
    semantic = exit_semantic(exit_code)
    if semantic.is_error:
        return Judgement(exit_code, semantic.meaning, None, True, None, (semantic.meaning,), None, False)
    if exit_code == 0 and (report is None or report.tests == 0):
        reason = NO_REPORT if report is None else NO_TESTS
        return Judgement(exit_code, semantic.meaning, None, True, reason, (reason,), None, False)
    unmet: list[str] = []
    if exit_code != 0:
        unmet.append(f"the test command exited {exit_code}")
    if report is not None:
        if report.failures:
            unmet.append(f"{report.failures} test(s) failed")
        if report.errors:
            unmet.append(f"{report.errors} test(s) errored")
        if report.skipped:
            unmet.append(f"{report.skipped} test(s) were skipped")
    left_out = collection.uncollected if collection is not None else None
    undeclared = collection.undeclared if collection is not None else ()
    unknown = collection.unknown if collection is not None else ()
    unmet.extend(unknown)
    blocking = [fact for fact, held in ((NOT_RUN, bool(left_out)), (UNDECLARED, bool(undeclared))) if held]
    unmet.extend(blocking)
    failed = exit_code != 0 or (report is not None and (report.failures or report.errors))
    outcome = Outcome.FAILED if failed else Outcome.PASSED
    stated: tuple[str, ...] = ()
    if phase == TddPhase.RED:
        meets = not blocking
        stated = unknown
    else:
        meets = not unmet
    return Judgement(exit_code, semantic.meaning, outcome, False, None, tuple(unmet), left_out, meets, undeclared,
                     stated)


__all__ = [
    "NO_REPORT",
    "NO_TESTS",
    "NOT_RUN",
    "UNDECLARED",
    "SHELL_EXIT_SEMANTICS",
    "SIGNAL_EXIT_BASE",
    "ExitSemantic",
    "Judgement",
    "Outcome",
    "exit_semantic",
    "judge",
]
