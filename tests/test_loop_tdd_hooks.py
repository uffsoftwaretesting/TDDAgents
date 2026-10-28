"""
Tests for tdd_phase_incomplete stop hook and stop_hook_active contract (Part D5).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.query.config import build_run_config
from app.query.deps import LoopDeps
from app.query.query import drain, run_loop
from app.query.state import initial_loop_state
from app.query.transitions import Continue, Terminal
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.stop_hook import tdd_phase_incomplete_hook
from app.tool import tool_context_for


class FakeModel:
    def __init__(self, turns: list[list[AIMessage]]) -> None:
        self.turns = list(turns)

    async def __call__(self, state, config):
        messages = self.turns.pop(0)
        for m in messages:
            yield m


def test_hook_allows_exit_when_cycle_complete():
    async def go():
        store = AppStateStore(
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True,
                                              refactor_passed=True))
        )
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("test-run", postgres_checkpointing=False)

        result = await tdd_phase_incomplete_hook(state, config)
        assert result.prevent_continuation is False
        assert len(result.blocking_errors) == 0

    asyncio.run(go())


RED_MSG = ("TDD cycle incomplete: RED is not done (tests_written=False). Write robust tests for this "
           "sub-requirement, covering every relevant scenario and edge case, and run RunTests.")
GREEN_MSG = ("TDD cycle incomplete: GREEN is not done (green_passed=False). Implement the code the tests "
             "describe and run RunTests until every test passes.")


def refactor_msg(figure: str) -> str:
    return ("TDD cycle incomplete: REFACTOR is not done (refactor_passed=False). It is mandatory: improve the "
            "code and the tests, then run RunTests until every test passes with line + branch coverage of at "
            f"least 90% (last measured: {figure}).")


@pytest.mark.parametrize("ledger, message", [
    (PhaseLedger(), RED_MSG),
    (PhaseLedger(green_passed=True, refactor_passed=True), RED_MSG),
    (PhaseLedger(phase=TddPhase.GREEN, tests_written=True), GREEN_MSG),
    (PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True), refactor_msg("not measured yet")),
    (PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True, coverage=84.25),
     refactor_msg("84.2%")),
])
def test_the_hook_names_what_the_cycle_still_needs(ledger, message):
    async def go():
        ctx = tool_context_for(AppStateStore(AppState(phase_ledger=ledger)))
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        result = await tdd_phase_incomplete_hook(state, build_run_config("test-run", postgres_checkpointing=False))
        assert result.prevent_continuation is False
        assert [m.content for m in result.blocking_errors] == [message]

    asyncio.run(go())


def test_hook_honours_stop_hook_active_contract():

    async def go():
        store = AppStateStore(
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED, tests_written=False, green_passed=False))
        )
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        # Simulate that the hook already blocked on this context
        state_with_active = type(state)(
            messages=state.messages,
            tool_context=state.tool_context,
            phase_ledger=state.phase_ledger,
            compaction_tracking=state.compaction_tracking,
            has_attempted_reactive_compact=state.has_attempted_reactive_compact,
            stop_hook_active=True,
            turn_count=state.turn_count,
            transition=state.transition,
        )
        config = build_run_config("test-run", postgres_checkpointing=False)

        result = await tdd_phase_incomplete_hook(state_with_active, config)
        # Must NOT return blocking errors again (prevents infinite loop!)
        assert result.prevent_continuation is True
        assert len(result.blocking_errors) == 0

    asyncio.run(go())


def test_stop_hook_in_full_loop_terminates_without_infinite_loop():
    async def go():
        store = AppStateStore(
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED, tests_written=False, green_passed=False))
        )
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("test-run", postgres_checkpointing=False)

        # Model repeatedly says "I'm done" without calling any tools
        model = FakeModel([
            [AIMessage(content="I claim done turn 1")],
            [AIMessage(content="I claim done turn 2")],
        ])

        async def run_tools_noop(calls, assistant_msgs, state, config):
            if False:
                yield

        events: list[Any] = []

        async def fake_compact(s, c):
            from app.query.state import CompactionResult
            return CompactionResult(compacted=False, messages=s.messages)

        deps = LoopDeps(
            call_model=model,
            compact=fake_compact,
            uuid=lambda: "u1",
            now=lambda: 1000.0,
            run_tools=run_tools_noop,
            stop_hooks=tdd_phase_incomplete_hook,
            emit_event=events.append,
        )

        terminated = await drain(run_loop(state, config, deps))

        # Because model did not fix the failing test, turn 1 blocked, turn 2 prevented continuation!
        assert terminated.reason == Terminal.STOP_HOOK_PREVENTED
        # Transition events contain STOP_HOOK_BLOCKING on turn 1
        reasons = [e.reason for e in events if hasattr(e, "reason")]
        assert Continue.STOP_HOOK_BLOCKING in reasons
        assert Terminal.STOP_HOOK_PREVENTED in reasons

    asyncio.run(go())
