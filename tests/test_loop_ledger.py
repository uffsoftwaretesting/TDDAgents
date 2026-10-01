"""
The phase ledger's shape, before anything writes it.

Nothing here asserts TDD behaviour — there is none yet. What it pins is that the ledger
starts at RED with nothing observed, so that Part D2's first write is provably a change
and not a coincidence, and that its three fields cannot be edited in place.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from app.loop.ledger import PhaseLedger


class TestStartingPosition:
    def test_a_run_starts_in_red(self):
        assert PhaseLedger().phase == "RED"

    def test_nothing_has_been_observed_yet(self):
        ledger = PhaseLedger()
        assert ledger.red_confirmed is False
        assert ledger.green_passed is False


class TestItIsAValueNotAHandle:
    def test_it_is_frozen(self):
        ledger = PhaseLedger()
        with pytest.raises(FrozenInstanceError):
            ledger.red_confirmed = True  # type: ignore[misc]

    def test_advancing_it_produces_a_new_ledger(self):
        ledger = PhaseLedger()
        advanced = replace(ledger, red_confirmed=True)
        assert advanced.red_confirmed is True
        assert ledger.red_confirmed is False

    def test_ledgers_compare_by_value(self):
        assert PhaseLedger(phase="GREEN", red_confirmed=True) == PhaseLedger(
            phase="GREEN", red_confirmed=True
        )
        assert PhaseLedger(phase="GREEN") != PhaseLedger(phase="RED")


class TestGreenInRedNeedsNoSpecialCase:
    def test_a_test_that_passed_first_try_is_just_a_ledger_state(self):
        """F2 in the paper: green observed, red never confirmed. No edge, no flag."""
        ledger = PhaseLedger(phase="GREEN", red_confirmed=False, green_passed=True)
        assert (ledger.red_confirmed, ledger.green_passed) == (False, True)


class TestTddPhaseValidation:
    def test_phase_accepts_tdd_phase_enum(self):
        from app.loop.ledger import TddPhase

        ledger = PhaseLedger(phase=TddPhase.GREEN)
        assert ledger.phase == TddPhase.GREEN

    def test_phase_accepts_valid_string_and_normalizes(self):
        from app.loop.ledger import TddPhase

        ledger = PhaseLedger(phase="REFACTOR")
        assert ledger.phase == TddPhase.REFACTOR
        assert isinstance(ledger.phase, TddPhase)

    def test_phase_rejects_invalid_string(self):
        with pytest.raises(ValueError, match="Invalid TDD phase: 'BLUE'"):
            PhaseLedger(phase="BLUE")


class TestWithTestResult:
    def test_failing_test_in_red_advances_to_green_and_confirms_red(self):
        from app.loop.ledger import TddPhase

        initial = PhaseLedger(phase=TddPhase.RED, red_confirmed=False, green_passed=False)
        updated = initial.with_test_result(exit_code=1)
        assert updated.phase == TddPhase.GREEN
        assert updated.red_confirmed is True
        assert updated.green_passed is False
        assert updated.is_cycle_complete is False

    def test_failing_test_in_green_leaves_green_passed_false(self):
        from app.loop.ledger import TddPhase

        initial = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        updated = initial.with_test_result(exit_code=1)
        assert updated.phase == TddPhase.GREEN
        assert updated.red_confirmed is True
        assert updated.green_passed is False

    def test_failing_test_in_refactor_marks_green_passed_false(self):
        from app.loop.ledger import TddPhase

        initial = PhaseLedger(phase=TddPhase.REFACTOR, red_confirmed=True, green_passed=True)
        updated = initial.with_test_result(exit_code=2)
        assert updated.phase == TddPhase.REFACTOR
        assert updated.green_passed is False
        assert updated.is_cycle_complete is False

    def test_passing_test_after_red_confirmed_marks_green_passed_true(self):
        from app.loop.ledger import TddPhase

        initial = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=False)
        updated = initial.with_test_result(exit_code=0)
        assert updated.phase == TddPhase.GREEN
        assert updated.red_confirmed is True
        assert updated.green_passed is True
        assert updated.is_cycle_complete is True

    def test_passing_test_without_red_confirmed_is_f2_case(self):
        from app.loop.ledger import TddPhase

        initial = PhaseLedger(phase=TddPhase.RED, red_confirmed=False, green_passed=False)
        updated = initial.with_test_result(exit_code=0)
        assert updated.phase == TddPhase.RED
        assert updated.red_confirmed is False
        assert updated.green_passed is True
        assert updated.is_cycle_complete is False


class TestTransitionTo:
    def test_transition_to_refactor_succeeds_when_cycle_complete(self):
        from app.loop.ledger import TddPhase

        initial = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        refactored = initial.transition_to(TddPhase.REFACTOR)
        assert refactored.phase == TddPhase.REFACTOR

    def test_transition_to_refactor_by_string_succeeds_when_cycle_complete(self):
        from app.loop.ledger import TddPhase

        initial = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        refactored = initial.transition_to("REFACTOR")
        assert refactored.phase == TddPhase.REFACTOR

    def test_transition_to_refactor_fails_when_cycle_incomplete(self):
        from app.loop.ledger import TddPhase

        initial = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=False)
        with pytest.raises(ValueError, match="Cannot transition to REFACTOR before cycle has passed GREEN"):
            initial.transition_to(TddPhase.REFACTOR)

    def test_transition_to_other_phases_succeeds(self):
        from app.loop.ledger import TddPhase

        initial = PhaseLedger(phase=TddPhase.RED)
        green = initial.transition_to(TddPhase.GREEN)
        assert green.phase == TddPhase.GREEN
