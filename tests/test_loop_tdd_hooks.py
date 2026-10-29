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


RED_RULE = ("RED ends with a run that passes, fails, or stops on collection errors pytest reports, after the "
            "sub-requirement's test files changed")
GREEN_RULE = ("GREEN ends with a run in which every test passes (exit 0), none is skipped and every test file is "
              "collected")
REFACTOR_RULE = ("REFACTOR ends with a run in which every test passes (exit 0), none is skipped and every test file "
                 "is collected")
WRITE_TESTS = ("Write or extend the tests for this sub-requirement, covering every relevant scenario and edge case, "
               "then run RunTests.")
#: A context with no workspace cannot read the test files.
RED_MSG = f"TDD cycle incomplete: RED. {RED_RULE}: they could not be read. {WRITE_TESTS}"
BACK_TO_RED = ("If a test is wrong (it contradicts the specification), go back to RED: spawn the tester agent "
               "(Agent with subagent_type 'tester') to fix it. That restarts this sub-requirement at RED; then "
               "implement again.")
NO_RUN = "no RunTests run has been recorded for it yet"
GREEN_MSG = (f"TDD cycle incomplete: GREEN. {GREEN_RULE}: {NO_RUN}. Implement the code the tests describe and run "
             f"RunTests until every test passes. {BACK_TO_RED}")


def refactor_msg(figure: str, run: str = NO_RUN) -> str:
    return (f"TDD cycle incomplete: REFACTOR. {REFACTOR_RULE}: {run}. It is mandatory: improve the code and the "
            "tests and raise line + branch coverage (a good rule of thumb is "
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


# ── the refusal states the rule and what was observed (2026-10-28) ──────────
#
# A live CPF run's plan held a sub-requirement whose behaviour an earlier one had already built.
# The old refusal said only "RED is not done (tests_written=False)", the agent took its earlier
# tests as this item's, and it asked the user the same question three times.

def _refusal(app_state: AppState, workspace: Any = None, tools: tuple[Any, ...] = ()) -> str:
    async def go() -> str:
        ctx = tool_context_for(AppStateStore(app_state), workspace=workspace, tools=tools)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        result = await tdd_phase_incomplete_hook(state, build_run_config("test-run", postgres_checkpointing=False))
        return str(result.blocking_errors[0].content)

    return asyncio.run(go())


def test_red_says_the_test_files_have_not_changed_since_the_item_started():
    from app.tdd.suite_fingerprint import fingerprint_test_files
    from tests.conftest import FakeWorkspace

    ws = FakeWorkspace(files={"tests/test_adapter.py": "def test_a():\n    pass\n"})
    baseline = fingerprint_test_files(ws)
    assert _refusal(AppState(tdd_item_test_baseline=baseline), ws) == (
        f"TDD cycle incomplete: RED. {RED_RULE}: they have not changed since this sub-requirement started. "
        f"{WRITE_TESTS}")
    ws.write_file("tests/test_adapter.py", "def test_a():\n    assert True\n")
    assert f"{RED_RULE}: they changed, and no RunTests run has counted since." in _refusal(
        AppState(tdd_item_test_baseline=baseline), ws)


def test_red_names_the_update_plan_way_only_when_the_pool_has_it():
    from types import SimpleNamespace

    from app.tdd.stop_hook import RED_DUPLICATE_WAY

    assert RED_DUPLICATE_WAY == (
        "If an earlier sub-requirement already built and tested this one's behaviour, you may instead propose an "
        "UpdatePlan change that merges or rewords it; the user approves it.")
    assert RED_DUPLICATE_WAY not in _refusal(AppState())
    with_update_plan = _refusal(AppState(), tools=(SimpleNamespace(name="UpdatePlan"),))
    assert with_update_plan.endswith(f"{WRITE_TESTS} {RED_DUPLICATE_WAY}")
    green = AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True))
    assert RED_DUPLICATE_WAY not in _refusal(green, tools=(SimpleNamespace(name="UpdatePlan"),))


@pytest.mark.parametrize("code, meaning", [(1, "some tests failed"), (2, "test execution was interrupted")])
def test_green_and_refactor_name_the_last_recorded_run(code, meaning):
    from app.tdd.ledger import TestRunRecord

    run = TestRunRecord(test_path=".", exit_code=code, stdout="", stderr="")
    green = AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True), last_test_run=run)
    assert f"{GREEN_RULE}: the last RunTests run exited {code} ({meaning})." in _refusal(green)
    refactoring = AppState(phase_ledger=PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True),
                           last_test_run=run)
    assert _refusal(refactoring) == refactor_msg("not measured yet", f"the last RunTests run exited {code} ({meaning})")
