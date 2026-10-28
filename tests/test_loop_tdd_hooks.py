"""
Tests for tdd_phase_incomplete stop hook and stop_hook_active contract (Part D5).
"""

from __future__ import annotations

import asyncio
import dataclasses
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
BACK_TO_RED = ("If a test is wrong (it contradicts the specification), go back to RED: spawn the tester agent "
               "(Agent with subagent_type 'tester') to fix it. That restarts this sub-requirement at RED; then "
               "implement again.")
GREEN_MSG = ("TDD cycle incomplete: GREEN is not done (green_passed=False). Implement the code the tests "
             f"describe and run RunTests until every test passes. {BACK_TO_RED}")


def refactor_msg(figure: str) -> str:
    return ("TDD cycle incomplete: REFACTOR is not done (refactor_passed=False). It is mandatory: improve the "
            "code and the tests and raise line + branch coverage (a good rule of thumb is "
            f"90%; last measured: {figure}), then run RunTests until every test passes.")


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


def test_the_hook_never_gives_up_even_when_stop_hook_active():
    """Phase 12 (user decision, as upstream): `stop_hook_active` is no latch; the block repeats."""

    async def go():
        store = AppStateStore(AppState(phase_ledger=PhaseLedger()))
        state = initial_loop_state((HumanMessage(content="task"),), tool_context_for(store))
        config = build_run_config("test-run", postgres_checkpointing=False)
        for active in (None, True, True):
            result = await tdd_phase_incomplete_hook(dataclasses.replace(state, stop_hook_active=active), config)
            assert result.prevent_continuation is False
            assert [m.content for m in result.blocking_errors] == [RED_MSG]

    asyncio.run(go())


def test_every_early_stop_in_the_loop_is_sent_back():
    async def go():
        store = AppStateStore(AppState(phase_ledger=PhaseLedger()))
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("test-run", postgres_checkpointing=False)
        claims = 4

        class ClaimsDone:
            calls = 0

            async def __call__(self, state, config):
                ClaimsDone.calls += 1
                if ClaimsDone.calls > claims:  # the user presses Esc
                    state.tool_context.cancel.cancel()
                yield AIMessage(content=f"I claim done ({ClaimsDone.calls})")

        async def run_tools_noop(calls, assistant_msgs, state, config):
            if False:
                yield

        events: list[Any] = []

        async def fake_compact(s, c):
            from app.query.state import CompactionResult
            return CompactionResult(compacted=False, messages=s.messages)

        deps = LoopDeps(call_model=ClaimsDone(), compact=fake_compact, uuid=lambda: "u1", now=lambda: 1000.0,
                        run_tools=run_tools_noop, stop_hooks=tdd_phase_incomplete_hook, emit_event=events.append)
        terminated = await drain(run_loop(state, config, deps))

        assert terminated.reason == Terminal.ABORTED_STREAMING
        reasons = [e.reason for e in events if hasattr(e, "reason")]
        assert reasons.count(Continue.STOP_HOOK_BLOCKING) == claims
        assert Terminal.STOP_HOOK_PREVENTED not in reasons

    asyncio.run(go())
