"""
The phase ledger (Phase 12 cycle): RED writes robust tests and ends on a run of changed tests,
whatever its outcome; GREEN ends on a passing run; REFACTOR is mandatory and ends on a passing
run with enough line + branch coverage.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from app.tdd.coverage_gate import UNAVAILABLE, CoverageReading, CoverageStatus
from app.tdd.ledger import PhaseLedger, TddPhase

MET = CoverageReading(CoverageStatus.MEASURED, 95.0)
UNDER = CoverageReading(CoverageStatus.MEASURED, 70.0)
NO_CODE = CoverageReading(CoverageStatus.NO_CODE)

GREEN = PhaseLedger(phase=TddPhase.GREEN, tests_written=True)
REFACTOR = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True)


class TestStartingPosition:
    def test_a_run_starts_in_red_with_nothing_observed(self):
        assert PhaseLedger() == PhaseLedger(phase=TddPhase.RED, tests_written=False, green_passed=False,
                                            refactor_passed=False, coverage=None)


class TestItIsAValueNotAHandle:
    def test_it_is_frozen(self):
        with pytest.raises(FrozenInstanceError):
            PhaseLedger().tests_written = True  # type: ignore[misc]

    def test_advancing_it_produces_a_new_ledger(self):
        ledger = PhaseLedger()
        assert replace(ledger, tests_written=True).tests_written is True
        assert ledger.tests_written is False

    def test_ledgers_compare_by_value(self):
        assert PhaseLedger(phase="GREEN", tests_written=True) == PhaseLedger(phase="GREEN", tests_written=True)
        assert PhaseLedger(phase="GREEN") != PhaseLedger(phase="RED")


class TestTddPhaseValidation:
    def test_phase_accepts_the_enum_and_normalizes_strings(self):
        assert PhaseLedger(phase=TddPhase.GREEN).phase == TddPhase.GREEN
        ledger = PhaseLedger(phase="REFACTOR")
        assert ledger.phase == TddPhase.REFACTOR and isinstance(ledger.phase, TddPhase)

    def test_phase_rejects_invalid_string(self):
        with pytest.raises(ValueError, match="^Invalid TDD phase: 'BLUE'. Must be one of: RED, GREEN, REFACTOR$"):
            PhaseLedger(phase="BLUE")


class TestRed:
    @pytest.mark.parametrize("exit_code", [0, 1])
    def test_a_run_of_changed_tests_ends_red_whatever_its_outcome(self, exit_code):
        updated = PhaseLedger().with_test_result(exit_code, tests_changed=True, coverage=MET)
        assert updated == PhaseLedger(phase=TddPhase.GREEN, tests_written=True)

    @pytest.mark.parametrize("exit_code", [0, 1])
    def test_a_run_with_no_changed_tests_stays_in_red(self, exit_code):
        assert PhaseLedger().with_test_result(exit_code, tests_changed=False, coverage=MET) == PhaseLedger()

    def test_no_evidence_by_default(self):
        assert PhaseLedger().with_test_result(0) == PhaseLedger()


class TestGreen:
    def test_a_pass_moves_to_refactor_without_passing_it(self):
        updated = GREEN.with_test_result(0, tests_changed=True, coverage=MET)
        assert updated == PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True,
                                      refactor_passed=False, coverage=95.0)
        assert updated.is_cycle_complete is False

    def test_a_failure_stays_in_green(self):
        assert replace(GREEN, green_passed=True).with_test_result(1, coverage=MET) == GREEN


class TestRefactor:
    def test_a_pass_with_enough_coverage_completes_the_cycle(self):
        updated = REFACTOR.with_test_result(0, coverage=MET)
        assert updated == replace(REFACTOR, refactor_passed=True, coverage=95.0)
        assert updated.is_cycle_complete is True

    def test_any_pass_completes_it_and_records_the_figure(self):
        """Coverage is a rule of thumb, not a gate (user decision, 2026-10-28)."""
        updated = REFACTOR.with_test_result(0, coverage=UNDER)
        assert updated == replace(REFACTOR, refactor_passed=True, coverage=70.0)
        assert updated.is_cycle_complete is True

    def test_no_production_code_passes_too(self):
        assert REFACTOR.with_test_result(0, coverage=NO_CODE) == replace(REFACTOR, refactor_passed=True)

    def test_unavailable_coverage_passes_and_records_nothing(self):
        assert REFACTOR.with_test_result(0, coverage=UNAVAILABLE) == replace(REFACTOR, refactor_passed=True)
        assert REFACTOR.with_test_result(0) == replace(REFACTOR, refactor_passed=True)

    def test_a_failure_un_passes_it_and_keeps_the_figure(self):
        passed = replace(REFACTOR, refactor_passed=True, coverage=95.0)
        assert passed.with_test_result(1, coverage=MET) == replace(passed, refactor_passed=False)


class TestCycleComplete:
    @pytest.mark.parametrize("ledger, complete", [
        (PhaseLedger(), False),
        (PhaseLedger(tests_written=True, green_passed=True), False),
        (PhaseLedger(green_passed=True, refactor_passed=True), False),
        (PhaseLedger(tests_written=True, refactor_passed=True), False),
        (PhaseLedger(tests_written=True, green_passed=True, refactor_passed=True), True),
    ])
    def test_it_needs_all_three_observations(self, ledger, complete):
        assert ledger.is_cycle_complete is complete


class TestTransitionTo:
    def test_green_needs_written_tests(self):
        with pytest.raises(ValueError, match=r"^Cannot transition to GREEN before RED's tests were written and run\.$"):
            PhaseLedger().transition_to(TddPhase.GREEN)
        assert REFACTOR.transition_to("GREEN") == replace(REFACTOR, phase=TddPhase.GREEN)

    def test_refactor_needs_a_passing_green(self):
        with pytest.raises(ValueError, match=r"^Cannot transition to REFACTOR before GREEN's tests passed\.$"):
            GREEN.transition_to(TddPhase.REFACTOR)
        passed = replace(GREEN, green_passed=True)
        assert passed.transition_to("REFACTOR") == replace(passed, phase=TddPhase.REFACTOR)

    def test_red_restarts_the_cycle(self):
        """A tester re-spawned rewrites the tests, so nothing observed survives."""
        done = replace(REFACTOR, refactor_passed=True, coverage=99.0)
        assert done.transition_to(TddPhase.RED) == PhaseLedger()
        assert done.transition_to("RED") == PhaseLedger()
