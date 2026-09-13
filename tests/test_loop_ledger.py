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
