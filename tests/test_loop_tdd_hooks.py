"""
Tests for tdd_phase_incomplete stop hook and stop_hook_active contract (Part D5).
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.loop.config import build_run_config
from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.deps import LoopDeps
from app.loop.engine import drain, run_loop
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.state import initial_loop_state
from app.loop.tdd.hooks import tdd_phase_incomplete_hook
from app.loop.transitions import Continue, Terminal


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
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True))
        )
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("test-run", postgres_checkpointing=False)

        result = await tdd_phase_incomplete_hook(state, config)
        assert result.prevent_continuation is False
        assert len(result.blocking_errors) == 0

    asyncio.run(go())


def test_hook_blocks_when_cycle_incomplete_in_red():
    async def go():
        store = AppStateStore(
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED, red_confirmed=False, green_passed=False))
        )
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("test-run", postgres_checkpointing=False)

        result = await tdd_phase_incomplete_hook(state, config)
        assert result.prevent_continuation is False
        assert len(result.blocking_errors) == 1
        assert (
            result.blocking_errors[0].content
            == "TDD cycle incomplete: no failing test observed yet in RED phase "
            "(red_confirmed=False). You must write a failing test and run RunTests."
        )

    asyncio.run(go())


def test_hook_blocks_when_f2_green_in_red_case():
    async def go():
        store = AppStateStore(
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED, red_confirmed=False, green_passed=True))
        )
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("test-run", postgres_checkpointing=False)

        result = await tdd_phase_incomplete_hook(state, config)
        assert result.prevent_continuation is False
        assert len(result.blocking_errors) == 1
        assert (
            result.blocking_errors[0].content
            == "TDD cycle incomplete: tests passed on first run without prior failure "
            "(red_confirmed=False). A failing test must be observed (RED phase) before implementation."
        )

    asyncio.run(go())


def test_hook_blocks_when_tests_still_failing_in_green():
    async def go():
        store = AppStateStore(
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=False))
        )
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("test-run", postgres_checkpointing=False)

        result = await tdd_phase_incomplete_hook(state, config)
        assert result.prevent_continuation is False
        assert len(result.blocking_errors) == 1
        assert (
            result.blocking_errors[0].content
            == "TDD cycle incomplete: tests are not passing yet in GREEN phase "
            "(green_passed=False). You must implement code and run RunTests until tests pass."
        )

    asyncio.run(go())


def test_hook_honours_stop_hook_active_contract():

    async def go():
        store = AppStateStore(
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED, red_confirmed=False, green_passed=False))
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
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED, red_confirmed=False, green_passed=False))
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
            from app.loop.deps import CompactionResult
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
