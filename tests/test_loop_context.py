"""
The ambient context: what a tool can reach, and what it cannot reach past.

The isolation property is the one worth having tests for. A worker handed
`discard_app_state_update` must be unable to affect the store its parent reads, and it
must fail that way silently rather than by raising — an isolated agent that crashed on
every write would be a different design.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest
from langchain_core.messages import HumanMessage

from app.loop.context import (
    AppState,
    AppStateStore,
    CancelToken,
    ToolContext,
    discard_app_state_update,
    tool_context_for,
)
from app.loop.ledger import PhaseLedger


def confirm_red(state: AppState) -> AppState:
    return replace(state, phase_ledger=replace(state.phase_ledger, red_confirmed=True))


class TestCancelToken:
    def test_it_starts_uncancelled(self):
        assert CancelToken().cancelled is False

    def test_cancelling_is_visible_to_whoever_holds_the_token(self):
        token = CancelToken()
        held_elsewhere = token
        token.cancel()
        assert held_elsewhere.cancelled is True

    def test_each_context_gets_its_own_token(self):
        """Part F3's abort tree needs separate controllers, so these must not be shared."""
        first = tool_context_for(AppStateStore())
        second = tool_context_for(AppStateStore())
        first.cancel.cancel()
        assert second.cancel.cancelled is False


class TestAppState:
    def test_it_is_frozen_so_a_holder_cannot_edit_it(self):
        state = AppState()
        with pytest.raises(FrozenInstanceError):
            state.phase_ledger = PhaseLedger(phase="GREEN")  # type: ignore[misc]

    def test_it_starts_with_an_untouched_ledger(self):
        assert AppState().phase_ledger == PhaseLedger()


class TestAppStateStore:
    def test_an_update_replaces_what_get_returns(self):
        store = AppStateStore()
        store.update(confirm_red)
        assert store.get().phase_ledger.red_confirmed is True

    def test_the_updater_is_handed_the_current_state(self):
        store = AppStateStore(state=AppState(phase_ledger=PhaseLedger(phase="GREEN")))
        seen: list[AppState] = []

        def record(state: AppState) -> AppState:
            seen.append(state)
            return state

        store.update(record)
        assert seen == [AppState(phase_ledger=PhaseLedger(phase="GREEN"))]

    def test_updates_compose(self):
        store = AppStateStore()
        store.update(confirm_red)
        store.update(lambda s: replace(s, phase_ledger=replace(s.phase_ledger, green_passed=True)))
        assert store.get().phase_ledger == PhaseLedger(red_confirmed=True, green_passed=True)

    def test_two_stores_do_not_see_each_other(self):
        """Per run, not per process — two runs in one interpreter must not share state."""
        first, second = AppStateStore(), AppStateStore()
        first.update(confirm_red)
        assert second.get().phase_ledger.red_confirmed is False


class TestIsolation:
    def test_a_discarded_write_leaves_the_parent_store_untouched(self):
        store = AppStateStore()
        worker = ToolContext(
            cancel=CancelToken(),
            get_app_state=store.get,
            set_app_state=discard_app_state_update,
        )
        worker.set_app_state(confirm_red)
        assert store.get().phase_ledger.red_confirmed is False

    def test_a_discarded_write_does_not_raise(self):
        """Isolation fails quietly. An agent that crashed on every write is a different design."""
        discard_app_state_update(confirm_red)


class TestToolContextWiring:
    def test_get_app_state_follows_the_store_rather_than_snapshotting_it(self):
        store = AppStateStore()
        context = tool_context_for(store)
        store.update(confirm_red)
        assert context.get_app_state().phase_ledger.red_confirmed is True

    def test_set_app_state_writes_through_to_the_store(self):
        store = AppStateStore()
        tool_context_for(store).set_app_state(confirm_red)
        assert store.get().phase_ledger.red_confirmed is True

    def test_messages_are_the_ones_handed_in(self):
        messages = (HumanMessage(content="spec"),)
        assert tool_context_for(AppStateStore(), messages=messages).messages == messages

    def test_messages_default_to_none_carried(self):
        assert tool_context_for(AppStateStore()).messages == ()

    def test_a_tool_cannot_append_to_the_conversation(self):
        context = tool_context_for(AppStateStore())
        with pytest.raises(AttributeError):
            context.messages.append(HumanMessage(content="smuggled"))  # type: ignore[attr-defined]

    def test_in_flight_ids_start_empty_and_are_not_shared_between_contexts(self):
        first = tool_context_for(AppStateStore())
        second = tool_context_for(AppStateStore())
        assert first.in_flight_tool_ids == set()
        first.in_flight_tool_ids.add("call-1")
        assert second.in_flight_tool_ids == set()

    def test_re_spreading_leaves_the_original_context_alone(self):
        context = tool_context_for(AppStateStore())
        successor = replace(context, messages=(HumanMessage(content="next"),))
        assert context.messages == ()
        assert successor.messages != ()
