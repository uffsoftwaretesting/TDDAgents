"""
The one judgement of a test run (`app/tdd/run_judgement.py`, 2026-11-03): the shell's exit codes,
then the matrix of exit × report × phase shared by RunTests and the clean final run.
"""

from __future__ import annotations

import pytest

from app.tdd.junit_report import JUnitReport
from app.tdd.ledger import TddPhase
from app.tdd.run_judgement import (
    NO_REPORT,
    NO_TESTS,
    SHELL_EXIT_SEMANTICS,
    SIGNAL_EXIT_BASE,
    ExitSemantic,
    Judgement,
    Outcome,
    exit_semantic,
    judge,
)

GREEN, RED, REFACTOR = TddPhase.GREEN, TddPhase.RED, TddPhase.REFACTOR
CLEAN = JUnitReport(tests=3, files=frozenset({"t_test.go"}))


@pytest.mark.parametrize(("code", "expected"), [
    (0, ExitSemantic("the test command completed", False, True)),
    (1, ExitSemantic("the test command failed", False, True)),
    (2, ExitSemantic("the test command failed", False, True)),
    (125, ExitSemantic("the test command failed", False, True)),
    (126, ExitSemantic("the test command is not executable", True, False)),
    (127, ExitSemantic("the test command was not found", True, False)),
    (128, ExitSemantic("the test command was killed by signal 0", True, False)),
    (137, ExitSemantic("the test command was killed by signal 9", True, False)),
    (255, ExitSemantic("the test command was killed by signal 127", True, False)),
    (-15, ExitSemantic("the test command was killed by signal 15", True, False)),
])
def test_exit_codes_read_as_a_shell_reads_them(code: int, expected: ExitSemantic):
    assert exit_semantic(code) == expected


def test_the_fixed_table_and_the_signal_base():
    assert sorted(SHELL_EXIT_SEMANTICS) == [0, 126, 127] and SIGNAL_EXIT_BASE == 128


@pytest.mark.parametrize("phase", list(TddPhase))
def test_a_clean_exit_zero_run_is_a_pass_in_every_phase(phase: TddPhase):
    assert judge(0, CLEAN, (), phase) == Judgement(0, "the test command completed", Outcome.PASSED, False, None, (),
                                                   (), True)
    assert judge(0, CLEAN, (), phase).passed


@pytest.mark.parametrize(("report", "reason"), [(None, NO_REPORT), (JUnitReport(tests=0), NO_TESTS)])
@pytest.mark.parametrize("phase", list(TddPhase))
def test_exit_zero_without_a_report_or_tests_is_not_evidence(report: JUnitReport | None, reason: str,
                                                             phase: TddPhase):
    judgement = judge(0, report, None, phase)
    assert (judgement.outcome, judgement.is_error, judgement.not_evidence, judgement.unmet, judgement.meets_phase) == (
        None, True, reason, (reason,), False)
    assert not judgement.passed
    assert (NO_REPORT, NO_TESTS) == ("the test command wrote no JUnit report", "the JUnit report records no tests")


@pytest.mark.parametrize("code", [126, 127, 130, -9])
def test_an_error_exit_is_not_evidence_whatever_the_report(code: int):
    judgement = judge(code, CLEAN, (), GREEN)
    assert (judgement.outcome, judgement.is_error, judgement.not_evidence, judgement.meets_phase) == (
        None, True, None, False)
    assert judgement.unmet == (exit_semantic(code).meaning,)


@pytest.mark.parametrize("report", [None, JUnitReport(tests=2, failures=1)])
def test_a_failed_run_is_evidence_even_without_a_report(report: JUnitReport | None):
    """A compile or load error counts as a failure (user decision)."""
    for phase in (GREEN, REFACTOR):
        judgement = judge(1, report, None, phase)
        assert (judgement.outcome, judgement.is_error, judgement.meets_phase) == (Outcome.FAILED, False, False)
        assert judgement.unmet[0] == "the test command exited 1"
    red = judge(2, report, None, RED)
    assert (red.outcome, red.meets_phase) == (Outcome.FAILED, True)


@pytest.mark.parametrize(("report", "unmet", "outcome"), [
    (JUnitReport(tests=3, skipped=1), ("1 test(s) were skipped",), Outcome.PASSED),
    (JUnitReport(tests=3, failures=2), ("2 test(s) failed",), Outcome.FAILED),
    (JUnitReport(tests=3, errors=1), ("1 test(s) errored",), Outcome.FAILED),
    (JUnitReport(tests=3, failures=1, errors=1, skipped=1),
     ("1 test(s) failed", "1 test(s) errored", "1 test(s) were skipped"), Outcome.FAILED),
])
def test_an_exit_zero_report_with_failures_or_skips_is_not_a_pass(report: JUnitReport, unmet: tuple[str, ...],
                                                                  outcome: Outcome):
    for phase in (GREEN, REFACTOR):
        judgement = judge(0, report, (), phase)
        assert (judgement.outcome, judgement.unmet, judgement.meets_phase, judgement.passed) == (
            outcome, unmet, False, False)
    # RED needs a completed run only: skips and failures do not matter there.
    assert judge(0, report, (), RED).meets_phase is True


def test_a_declared_test_file_that_did_not_run_keeps_every_phase_from_its_rule():
    for phase in list(TddPhase):
        judgement = judge(0, CLEAN, ("b_test.go",), phase)
        assert judgement.uncollected == ("b_test.go",)
        assert judgement.unmet == ("a declared test file did not run",)
        assert (judgement.meets_phase, judgement.passed) == (False, False)


def test_which_files_ran_is_judged_only_on_exit_zero_runs_and_may_be_unknown():
    failed = judge(1, JUnitReport(tests=1, failures=1), ("b_test.go",), RED)
    assert (failed.uncollected, failed.meets_phase) == (None, True)
    unknown = judge(0, JUnitReport(tests=1), None, GREEN)
    assert (unknown.uncollected, unknown.passed) == (None, True)
