"""Phase 7: the ledger transitions the Agent tool may request on spawn."""

from __future__ import annotations

import pytest

from app.loop.ledger import PhaseLedger, TddPhase


def test_green_needs_a_confirmed_red():
    message = "^Cannot transition to GREEN before a failing test was observed \\(RED\\)\\.$"
    with pytest.raises(ValueError, match=message):
        PhaseLedger().transition_to(TddPhase.GREEN)
    ledger = PhaseLedger(phase=TddPhase.REFACTOR, red_confirmed=True, green_passed=True)
    assert ledger.transition_to(TddPhase.GREEN) == PhaseLedger(
        phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)


def test_red_restarts_the_cycle():
    """A tester re-spawned after RED rewrites the test, so nothing observed survives."""
    ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
    assert ledger.transition_to(TddPhase.RED) == PhaseLedger()
    assert ledger.transition_to("RED") == PhaseLedger()
