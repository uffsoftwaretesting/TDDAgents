"""
Phase 11: the ledger-driven advance from one sub-requirement to the next
(`app/tdd/plan_advance.py`), the per-item stop-hook latch and the plan in the TDD state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.query.config import build_run_config
from app.query.deps import LoopDeps
from app.query.query import run_loop
from app.query.state import CompactionResult, initial_loop_state
from app.query.stop_hooks import StopHookResult, build_stop_hooks_runner
from app.query.transitions import Continue, Terminal, Terminated, Transition
from app.services.tools.tool_orchestration import run_tools
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase, TestRunRecord
from app.tdd.plan import PLAN_FILE_NAME, ItemStatus, TddPlan, load_plan
from app.tdd.plan_advance import advance_plan, next_item_message
from app.tdd.run_tests_tool.run_tests_tool import (
    SuiteExecutionResult,
    build_run_tests_tool,
)
from app.tdd.stop_hook import tdd_phase_incomplete_hook
from app.tdd.tdd_state_attachment import (
    create_tdd_state_attachment_message,
    get_tdd_state_attachments,
    tdd_state_data,
)
from app.tool import tool_context_for

CYCLE = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
CONFIG = build_run_config("advance", postgres_checkpointing=False)


def state_for(store: AppStateStore, *, stop_hook_active: bool | None = None, plan_file: Path | None = None) -> Any:
    ctx = tool_context_for(store)
    if plan_file is not None:
        ctx.tdd_plan_path = str(plan_file)
    state = initial_loop_state((HumanMessage(content="go"),), ctx)
    return replace(state, stop_hook_active=stop_hook_active)


RED = PhaseLedger()


def store_with(plan: TddPlan, ledger: PhaseLedger = RED, **kw: Any) -> AppStateStore:
    return AppStateStore(state=AppState(tdd_plan=plan, phase_ledger=ledger, **kw))


# ── advance_plan ─────────────────────────────────────────────────────────────

def test_without_a_plan_the_tdd_hook_decides():
    assert advance_plan(state_for(AppStateStore())) is None


def test_an_incomplete_cycle_leaves_the_decision_to_the_tdd_hook():
    store = store_with(TddPlan.from_texts(["A"]), PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True))
    assert advance_plan(state_for(store)) is None
    current = store.get().tdd_plan.current
    assert current is not None and current.id == "1"


def test_a_completed_cycle_advances_resets_the_ledger_and_announces_the_next_item(tmp_path: Path):
    record = TestRunRecord(test_path="t", exit_code=0)
    store = store_with(TddPlan.from_texts(["A", "B"]), CYCLE, last_test_run=record)
    result = advance_plan(state_for(store, plan_file=tmp_path / PLAN_FILE_NAME))
    plan = store.get().tdd_plan
    assert [i.status for i in plan.items] == [ItemStatus.DONE, ItemStatus.PENDING]
    assert store.get().phase_ledger == PhaseLedger()
    assert store.get().last_test_run is None
    assert load_plan(tmp_path / PLAN_FILE_NAME) == plan
    assert result is not None and not result.prevent_continuation
    assert [m.content for m in result.blocking_errors] == [next_item_message(TddPlan.from_texts(["A"]).items[0], plan)]


def test_the_next_item_message_names_it_and_its_position():
    plan = TddPlan.from_texts(["A", "B", "C"]).complete_current(CYCLE)
    assert next_item_message(plan.items[0], plan) == (
        "Sub-requirement 1 is done: RunTests observed its failing test and then the passing one.\n\n"
        "Next sub-requirement (2 of 3): 2. B\n\n"
        "The TDD ledger is back in RED for it: write a failing test for this sub-requirement and run "
        "RunTests before implementing it, and implement only what its tests demand.\n\n"
        "Plan:\n✔ 1. A\n▶ 2. B\n· 3. C"
    )


def test_completing_the_last_item_lets_the_turn_end_completed():
    store = store_with(TddPlan.from_texts(["A"]), CYCLE)
    assert advance_plan(state_for(store)) == StopHookResult()
    assert store.get().tdd_plan.is_complete
    assert store.get().phase_ledger == PhaseLedger()


def test_a_finished_plan_lets_any_later_turn_end():
    store = store_with(TddPlan.from_texts(["A"]).complete_current(CYCLE))
    assert advance_plan(state_for(store)) == StopHookResult()


def test_paused_ends_the_turn_without_completing():
    store = store_with(TddPlan.from_texts(["A", "B"]), tdd_paused=True)
    assert advance_plan(state_for(store)) == StopHookResult(prevent_continuation=True)


def test_paused_still_records_a_completed_cycle_before_stopping():
    store = store_with(TddPlan.from_texts(["A", "B"]), CYCLE, tdd_paused=True)
    assert advance_plan(state_for(store)) == StopHookResult(prevent_continuation=True)
    assert store.get().tdd_plan.done_count == 1


def test_paused_on_the_last_item_with_its_cycle_complete_is_completed():
    store = store_with(TddPlan.from_texts(["A"]), CYCLE, tdd_paused=True)
    assert advance_plan(state_for(store)) == StopHookResult()


# ── the per-item latch of the TDD hook ───────────────────────────────────────

def hook(store: AppStateStore, active: bool | None) -> StopHookResult:
    return asyncio.run(tdd_phase_incomplete_hook(state_for(store, stop_hook_active=active), CONFIG))


def test_without_a_plan_the_latch_is_part_d5s_one_block():
    store = AppStateStore()
    first = hook(store, None)
    assert first.blocking_errors and store.get().tdd_blocked_item is None
    assert hook(store, True) == StopHookResult(prevent_continuation=True)


def test_the_block_names_the_current_sub_requirement():
    store = store_with(TddPlan.from_texts(["Add numbers"]))
    result = hook(store, None)
    content = str(result.blocking_errors[0].content)
    assert content.startswith("Current sub-requirement 1: Add numbers\n\nTDD cycle incomplete:")
    assert store.get().tdd_blocked_item == "1"


def test_the_latch_is_per_item():
    plan = TddPlan.from_texts(["A", "B"])
    store = store_with(plan, tdd_blocked_item="1")
    assert hook(store, True) == StopHookResult(prevent_continuation=True)
    store = store_with(plan.complete_current(CYCLE), tdd_blocked_item="1")
    blocked = hook(store, True)
    assert blocked.blocking_errors and not blocked.prevent_continuation
    assert store.get().tdd_blocked_item == "2"


def test_a_complete_cycle_never_blocks():
    assert hook(store_with(TddPlan.from_texts(["A"]), CYCLE), True) == StopHookResult()


# ── the TDD state attachment carries the plan ────────────────────────────────

def test_tdd_state_data_adds_the_current_item_and_progress_only_with_a_plan():
    assert tdd_state_data(PhaseLedger()) == {"phase": "RED", "red_confirmed": False, "green_passed": False}
    assert tdd_state_data(PhaseLedger(), TddPlan()) == tdd_state_data(PhaseLedger())
    plan = TddPlan.from_texts(["A", "B"]).complete_current(CYCLE)
    assert tdd_state_data(PhaseLedger(), plan) == {
        "phase": "RED", "red_confirmed": False, "green_passed": False,
        "sub_requirement": "2. B", "plan_progress": "1/2"}
    assert tdd_state_data(PhaseLedger(), plan.complete_current(CYCLE))["sub_requirement"] is None


def test_the_state_is_announced_again_when_the_plan_advances():
    store = store_with(TddPlan.from_texts(["A", "B"]))
    ctx = tool_context_for(store)
    first = get_tdd_state_attachments([], ctx)
    message = create_tdd_state_attachment_message(PhaseLedger(), None, after_compaction=False,
                                                  plan=store.get().tdd_plan)
    assert first and get_tdd_state_attachments([message], ctx) == []
    store.update(lambda s: replace(s, tdd_plan=s.tdd_plan.complete_current(CYCLE)))
    assert get_tdd_state_attachments([message], ctx)[0].data["sub_requirement"] == "2. B"


def test_the_rendered_state_names_the_item_and_after_compaction_the_whole_plan():
    plan = TddPlan.from_texts(["A", "B"]).complete_current(CYCLE)
    live = create_tdd_state_attachment_message(PhaseLedger(), None, after_compaction=False, plan=plan)
    assert "Current Sub-requirement: 2. B\nSub-requirements Done: 1/2" in live.content
    assert "Plan (recorded" not in live.content
    compacted = create_tdd_state_attachment_message(PhaseLedger(), None, after_compaction=True, plan=plan)
    assert "Plan (recorded by the system, not by the model):\n✔ 1. A\n▶ 2. B" in compacted.content
    done = create_tdd_state_attachment_message(PhaseLedger(), None, after_compaction=False,
                                               plan=plan.complete_current(CYCLE))
    assert "Current Sub-requirement: (all done)" in done.content


# ── the loop through two sub-requirements ────────────────────────────────────

class Scripted:
    def __init__(self, turns: list[list[AIMessage]]) -> None:
        self.turns = list(turns)
        self.seen: list[Any] = []

    async def __call__(self, state: Any, config: Any) -> AsyncIterator[AIMessage]:
        self.seen.append(state.messages)
        for message in self.turns.pop(0) if self.turns else [AIMessage(content="stop")]:
            yield message


def run_tests_call(n: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": f"t{n}", "name": "RunTests", "args": {"test_path": "tests"},
                                              "type": "tool_call"}])


def drive(store: AppStateStore, model: Scripted, exit_codes: list[int]) -> tuple[Terminated, list[Any]]:
    codes = list(exit_codes)
    tools = [build_run_tests_tool(lambda path: SuiteExecutionResult(exit_code=codes.pop(0)))]

    async def run_tools_impl(calls: Any, assistant: Any, state: Any, config: Any) -> AsyncIterator[Any]:
        async for r in run_tools(calls, assistant, state, config, tools=tools):
            yield r

    async def no_compact(s: Any, c: Any) -> CompactionResult:
        return CompactionResult(compacted=False, messages=s.messages)

    events: list[Any] = []
    deps = LoopDeps(call_model=model, compact=no_compact, uuid=lambda: "u", now=lambda: 1.0,
                    run_tools=run_tools_impl, stop_hooks=build_stop_hooks_runner(), emit_event=events.append)

    async def go() -> Terminated:
        state = initial_loop_state((HumanMessage(content="start"),), tool_context_for(store, tools=tuple(tools)))
        last = None
        async for event in run_loop(state, CONFIG, deps):
            if isinstance(event, Terminated):
                last = event
        assert last is not None
        return last

    return asyncio.run(go()), events


def test_one_conversation_works_through_every_item_and_only_then_completes():
    store = store_with(TddPlan.from_texts(["A", "B"]))
    model = Scripted([
        [run_tests_call(1)], [run_tests_call(2)], [AIMessage(content="A done")],
        [run_tests_call(3)], [run_tests_call(4)], [AIMessage(content="B done")],
    ])
    terminal, events = drive(store, model, [1, 0, 1, 0])
    assert terminal == Terminated(reason=Terminal.COMPLETED)
    assert store.get().tdd_plan.is_complete
    assert Transition(reason=Continue.STOP_HOOK_BLOCKING) in events
    announced = [m for m in model.seen[3] if isinstance(m, HumanMessage) and "Next sub-requirement" in str(m.content)]
    assert announced and "(2 of 2): 2. B" in announced[0].content


def test_a_second_item_started_green_in_red_cannot_complete_the_plan():
    store = store_with(TddPlan.from_texts(["A", "B"]))
    model = Scripted([
        [run_tests_call(1)], [run_tests_call(2)], [AIMessage(content="A done")],
        [run_tests_call(3)], [AIMessage(content="B passes already, done")], [AIMessage(content="really done")],
    ])
    terminal, _ = drive(store, model, [1, 0, 0])
    assert terminal == Terminated(reason=Terminal.STOP_HOOK_PREVENTED)
    assert store.get().tdd_plan.done_count == 1
