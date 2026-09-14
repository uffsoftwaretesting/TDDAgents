"""
The per-iteration record: what it refuses to let a continue site do.

Every assertion here is about a property the record is supposed to enforce by
construction — immutability that reaches the collections, no field a site may leave
unnamed, and one definition of what a run starts as. The behaviour these protect does not
exist yet; the point is that it cannot be built wrong later.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, MISSING, fields, replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.loop.context import AppStateStore, tool_context_for
from app.loop.ledger import PhaseLedger
from app.loop.state import CompactionTracking, LoopState, initial_loop_state


def make_state(**overrides: Any) -> LoopState:
    """A complete record, so a test can vary exactly one thing about it."""
    base: dict[str, Any] = dict(
        messages=(HumanMessage(content="spec"),),
        tool_context=tool_context_for(AppStateStore()),
        phase_ledger=PhaseLedger(),
        compaction_tracking=None,
        has_attempted_reactive_compact=False,
        stop_hook_active=None,
        turn_count=1,
        transition=None,
    )
    base.update(overrides)
    return LoopState(**base)


class TestWholesaleReplacement:
    def test_a_field_cannot_be_reassigned(self):
        state = make_state()
        with pytest.raises(FrozenInstanceError):
            state.turn_count = 2  # type: ignore[misc]

    def test_messages_cannot_be_appended_to(self):
        """The half of immutability `frozen=True` does not give: no in-place edit."""
        state = make_state()
        with pytest.raises(AttributeError):
            state.messages.append(AIMessage(content="late"))  # type: ignore[attr-defined]

    def test_no_attribute_can_be_added(self):
        """
        `slots=True`, so there is no instance dict for a typo to land in. The exception
        type is not asserted: CPython raises `FrozenInstanceError` for a declared field
        but trips its own stale-closure bug on an undeclared one and raises `TypeError`.
        What matters is that neither write lands.
        """
        state = make_state()
        assert not hasattr(state, "__dict__")
        with pytest.raises((AttributeError, TypeError)):
            state.improvised_field = True  # type: ignore[attr-defined]

    def test_replacing_produces_a_new_record_and_leaves_the_old_one_alone(self):
        state = make_state(turn_count=3)
        successor = replace(state, turn_count=4)
        assert successor.turn_count == 4
        assert state.turn_count == 3
        assert successor is not state


class TestEveryFieldIsNamed:
    def test_no_field_carries_a_default(self):
        """
        A default would let a continue site stay silent about a field, which is the
        silence the wholesale-replacement discipline exists to remove.
        """
        for field_ in fields(LoopState):
            assert field_.default is MISSING, field_.name
            assert field_.default_factory is MISSING, field_.name

    def test_omitting_a_field_is_an_error(self):
        with pytest.raises(TypeError):
            LoopState(  # type: ignore[call-arg]
                messages=(),
                tool_context=tool_context_for(AppStateStore()),
                phase_ledger=PhaseLedger(),
                compaction_tracking=None,
                has_attempted_reactive_compact=False,
                stop_hook_active=None,
                turn_count=1,
            )

    def test_the_record_carries_exactly_the_agreed_fields(self):
        """
        Pinned because the two counters were dropped deliberately when the ceilings were:
        re-adding one silently would re-add a bound nothing decided to have.
        """
        assert [f.name for f in fields(LoopState)] == [
            "messages",
            "tool_context",
            "phase_ledger",
            "compaction_tracking",
            "has_attempted_reactive_compact",
            "stop_hook_active",
            "turn_count",
            "transition",
        ]


class TestInitialState:
    def test_turn_count_starts_at_one(self):
        """Not zero: the first model call is a turn already under way."""
        assert initial_loop_state((), tool_context_for(AppStateStore())).turn_count == 1

    def test_nothing_has_happened_yet(self):
        state = initial_loop_state((), tool_context_for(AppStateStore()))
        assert state.transition is None
        assert state.compaction_tracking is None
        assert state.has_attempted_reactive_compact is False
        assert state.stop_hook_active is None

    def test_the_ledger_starts_with_nothing_observed(self):
        ledger = initial_loop_state((), tool_context_for(AppStateStore())).phase_ledger
        assert ledger == PhaseLedger(phase="RED", red_confirmed=False, green_passed=False)

    def test_messages_and_context_are_the_ones_handed_in(self):
        messages = (HumanMessage(content="spec"),)
        context = tool_context_for(AppStateStore())
        state = initial_loop_state(messages, context)
        assert state.messages == messages
        assert state.tool_context is context


class TestCompactionTracking:
    def test_it_carries_the_ported_members(self):
        tracking = CompactionTracking(compacted=True, turn_id="turn-1", turn_counter=2)
        assert (tracking.compacted, tracking.turn_id, tracking.turn_counter) == (True, "turn-1", 2)

    def test_it_does_not_count_failures(self):
        """
        Upstream's `consecutiveFailures` feeds one circuit-breaker comparison and nothing
        else. With no ceiling there is no reader, and a field nothing reads is how a bound
        gets quietly reintroduced later.
        """
        assert [f.name for f in fields(CompactionTracking)] == [
            "compacted",
            "turn_id",
            "turn_counter",
        ]

    def test_it_is_frozen(self):
        tracking = CompactionTracking(compacted=False, turn_id="turn-1", turn_counter=0)
        with pytest.raises(FrozenInstanceError):
            tracking.turn_counter = 1  # type: ignore[misc]
