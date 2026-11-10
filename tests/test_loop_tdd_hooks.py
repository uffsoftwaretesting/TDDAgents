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
from app.query.transitions import Continue, Terminal, Terminated
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
        config = build_run_config("test-run")

        result = await tdd_phase_incomplete_hook(state, config)
        assert result.prevent_continuation is False
        assert len(result.blocking_errors) == 0

    asyncio.run(go())


RED_RULE = ("RED ends with a completed run of the test command (its tests pass or fail), in which no declared test "
            "file is named as not run and no file the declaration does not cover ran, after the sub-requirement's "
            "test files changed")
GREEN_RULE = ("GREEN ends with a run that exits 0, whose JUnit report records tests, none failed, errored or "
              "skipped, and in which every declared test file is named as run (by the JUnit report, else by the "
              "listTests command) and no file the declaration does not cover ran")
REFACTOR_RULE = ("REFACTOR ends with a run that exits 0, whose JUnit report records tests, none failed, errored "
                 "or skipped, and in which every declared test file is named as run (by the JUnit report, else by "
                 "the listTests command) and no file the declaration does not cover ran")
WRITE_TESTS = ("Write or extend the tests for this sub-requirement, covering every relevant scenario and edge case, "
               "then run RunTests.")
#: A context with no workspace cannot read the test files.
RED_MSG = f"TDD cycle incomplete: RED. {RED_RULE}: they could not be read. {WRITE_TESTS}"
BACK_TO_RED = ("If a test is wrong (it contradicts the specification), go back to RED: spawn the tester agent "
               "(Agent with subagent_type 'tester') to fix it. That restarts this sub-requirement at RED; then "
               "implement again.")


def no_run(phase: str) -> str:
    return f"no RunTests run since {phase} started"


NO_RUN = no_run("GREEN")
GREEN_MSG = (f"TDD cycle incomplete: GREEN. {GREEN_RULE}: {NO_RUN}. Implement the code the tests describe and run "
             f"RunTests until every test passes. {BACK_TO_RED}")


def refactor_msg(figure: str, run: str = no_run("REFACTOR")) -> str:
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
        result = await tdd_phase_incomplete_hook(state, build_run_config("test-run"))
        assert result.prevent_continuation is False
        assert [m.content for m in result.blocking_errors] == [f"Stop hook feedback:\n{message}"]
        assert all(m.additional_kwargs == {"is_meta": True} for m in result.blocking_errors)

    asyncio.run(go())


def test_the_hook_never_gives_up_even_when_stop_hook_active():
    """Phase 12 (user decision, as upstream): `stop_hook_active` is no latch; the block repeats."""

    async def go():
        store = AppStateStore(AppState(phase_ledger=PhaseLedger()))
        state = initial_loop_state((HumanMessage(content="task"),), tool_context_for(store))
        config = build_run_config("test-run")
        for active in (None, True, True):
            result = await tdd_phase_incomplete_hook(dataclasses.replace(state, stop_hook_active=active), config)
            assert result.prevent_continuation is False
            assert [m.content for m in result.blocking_errors] == [f"Stop hook feedback:\n{RED_MSG}"]

    asyncio.run(go())


def test_every_early_stop_in_the_loop_is_sent_back():
    async def go():
        store = AppStateStore(AppState(phase_ledger=PhaseLedger()))
        ctx = tool_context_for(store)
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        config = build_run_config("test-run")
        claims = 2  # a third text-only stop in a row would end the turn (2026-11-10)

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
        result = await tdd_phase_incomplete_hook(state, build_run_config("test-run"))
        content = str(result.blocking_errors[0].content)
        # `handleStopHooks`' shape (2026-11-08): the feedback prefix on a meta user message.
        assert content.startswith("Stop hook feedback:\n") and result.blocking_errors[0].additional_kwargs == {
            "is_meta": True}
        return content.removeprefix("Stop hook feedback:\n")

    return asyncio.run(go())


def test_red_says_the_test_files_have_not_changed_since_the_item_started():
    from app.tdd.suite_fingerprint import fingerprint_test_files
    from tests.conftest import FakeWorkspace

    ws = FakeWorkspace(files={"tests/test_adapter.py": "def test_a():\n    pass\n"})
    from tests.plan_helpers import TEST_PROJECT_CONFIG

    baseline = fingerprint_test_files(ws, TEST_PROJECT_CONFIG)
    assert _refusal(AppState(tdd_item_test_baseline=baseline, project_config=TEST_PROJECT_CONFIG), ws) == (
        f"TDD cycle incomplete: RED. {RED_RULE}: they have not changed since this sub-requirement started. "
        f"{WRITE_TESTS}")
    ws.write_file("tests/test_adapter.py", "def test_a():\n    assert True\n")
    assert f"{RED_RULE}: they changed, and no RunTests run has counted since." in _refusal(
        AppState(tdd_item_test_baseline=baseline, project_config=TEST_PROJECT_CONFIG), ws)


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


@pytest.mark.parametrize("code, meaning", [(1, "the test command failed"), (2, "the test command failed")])
def test_green_and_refactor_name_the_last_run_of_their_own_phase(code, meaning):
    from app.tdd.ledger import TestRunRecord

    def run_in(phase: TddPhase) -> TestRunRecord:
        return TestRunRecord(command="make test", exit_code=code, stdout="", stderr="", meaning=meaning, phase=phase)

    green = AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True),
                     last_test_run=run_in(TddPhase.GREEN))
    assert f"{GREEN_RULE}: the last RunTests run in GREEN exited {code} ({meaning}) and did not meet it." in _refusal(
        green)
    refactoring = AppState(phase_ledger=PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True),
                           last_test_run=run_in(TddPhase.REFACTOR))
    assert _refusal(refactoring) == refactor_msg(
        "not measured yet", f"the last RunTests run in REFACTOR exited {code} ({meaning}) and did not meet it")


@pytest.mark.parametrize("phase, earlier", [(TddPhase.REFACTOR, TddPhase.GREEN), (TddPhase.GREEN, TddPhase.RED)])
def test_a_passing_run_from_the_phase_before_is_never_named(phase, earlier):
    """
    The live CPF run (2026-10-28): in REFACTOR the refusal said "the last RunTests run exited 0", the run
    that ended GREEN, and the agent declared REFACTOR done three times without running RunTests.
    """
    from app.tdd.ledger import TestRunRecord

    ledger = PhaseLedger(phase=phase, tests_written=True, green_passed=phase == TddPhase.REFACTOR)
    for record in (TestRunRecord(command=".", exit_code=0, phase=earlier), TestRunRecord(command=".", exit_code=0),
                   None):
        refusal = _refusal(AppState(phase_ledger=ledger, last_test_run=record))
        assert f": {no_run(phase.value)}." in refusal and "exited 0" not in refusal


# ── no declared configuration while an item is current (user decision 2026-11-03) ──────────
#
# The tier fails closed then (`NO_PROJECT_CONFIG`). The RED refusal used to say the test files "could
# not be read", a fact the missing declaration caused, so the model never learned to declare it.

NO_CONFIG_REASON = (
    "the project declares no test command, so its test files cannot be told from its production code. "
    'The project\'s configuration is declared in the plan\'s "## Project Configuration" section and approved '
    "with the plan (ExitPlanMode).")


@pytest.mark.parametrize("ledger, rule", [
    (PhaseLedger(), RED_RULE),
    (PhaseLedger(phase=TddPhase.GREEN, tests_written=True), GREEN_RULE),
    (PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True, coverage=50.0), REFACTOR_RULE),
])
def test_every_phase_states_the_tiers_reason_when_no_configuration_is_declared(ledger, rule):
    from types import SimpleNamespace

    from app.tdd.ledger import TestRunRecord
    from app.tdd.phase_permissions import NO_PROJECT_CONFIG
    from tests.conftest import FakeWorkspace
    from tests.plan_helpers import CURRENT_ITEM_PLAN

    assert NO_PROJECT_CONFIG == NO_CONFIG_REASON
    phase = ledger.phase.value
    record = TestRunRecord(command=".", exit_code=1, meaning="the test command failed", phase=ledger.phase)
    ws = FakeWorkspace(files={"tests/test_adapter.py": "def test_a():\n    pass\n"})
    refusal = _refusal(AppState(phase_ledger=ledger, tdd_plan=CURRENT_ITEM_PLAN, last_test_run=record), ws,
                       tools=(SimpleNamespace(name="UpdatePlan"),))
    item = CURRENT_ITEM_PLAN.current
    assert item is not None
    assert refusal == (f"Current sub-requirement {item.id}: {item.text}\n\n"
                       f"TDD cycle incomplete: {phase}. {rule}: {NO_CONFIG_REASON}")


def test_an_invalid_declaration_is_named_in_the_stop_hooks_refusal():
    from tests.plan_helpers import CURRENT_ITEM_PLAN

    refusal = _refusal(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config_issues=("a", "b")))
    assert refusal.endswith(f"{NO_CONFIG_REASON} .tddagents/project.json is invalid, so it declares nothing: a; b.")


def test_without_a_current_item_a_missing_declaration_keeps_the_observed_fact():
    """The tier fails closed only while an item is current; otherwise the refusal is unchanged."""
    assert _refusal(AppState()) == RED_MSG
    assert _refusal(AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True))) == GREEN_MSG


def test_with_a_declaration_and_a_current_item_the_refusal_states_the_observed_fact():
    from tests.conftest import FakeWorkspace
    from tests.plan_helpers import CURRENT_ITEM_PLAN, TEST_PROJECT_CONFIG

    ws = FakeWorkspace(files={"tests/test_adapter.py": "def test_a():\n    pass\n"})
    refusal = _refusal(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=TEST_PROJECT_CONFIG), ws)
    assert refusal.endswith(f"TDD cycle incomplete: RED. {RED_RULE}: they changed, and no RunTests run has counted "
                            f"since. {WRITE_TESTS}")
    assert NO_CONFIG_REASON not in refusal


# ── repeated text-only stops end the turn (user decision 2026-11-10) ─────────

def _fb(text: str = "TDD cycle incomplete: RED.") -> HumanMessage:
    return HumanMessage(content=f"Stop hook feedback:\n{text}", additional_kwargs={"is_meta": True})


def test_blocked_text_only_stops_counts_the_trailing_groups():
    from langchain_core.messages import ToolMessage

    from app.tdd.stop_hook import blocked_text_only_stops

    task, wait = HumanMessage(content="task"), AIMessage(content="waiting")
    call = AIMessage(content="", tool_calls=[{"name": "RunTests", "id": "c1", "args": {}}])
    assert blocked_text_only_stops([task, wait]) == 0
    assert blocked_text_only_stops([task, wait, _fb(), wait]) == 1
    assert blocked_text_only_stops([task, wait, _fb(), wait, _fb(), wait]) == 2
    # One response split in two text blocks, and two hooks' feedback, are one group each.
    assert blocked_text_only_stops([task, wait, _fb(), _fb("lint"), wait, wait, _fb(), wait]) == 2
    # A tool call, a tool result or the user's own message ends the run.
    assert blocked_text_only_stops([task, wait, _fb(), call, ToolMessage(content="x", tool_call_id="c1"),
                                    wait, _fb(), wait]) == 1
    assert blocked_text_only_stops([task, wait, _fb(), wait, HumanMessage(content="go on"), wait, _fb(), wait]) == 1
    assert blocked_text_only_stops([task, wait, _fb(), call]) == 0
    assert blocked_text_only_stops([]) == 0


def test_the_third_text_only_stop_in_a_row_ends_the_turn_as_not_completed():
    """A model that can only wait repeated itself 273 times in a live run; the cycle stays incomplete."""
    model = FakeModel([[AIMessage(content="waiting")], [AIMessage(content="waiting")],
                       [AIMessage(content="waiting")]])

    async def go():
        store = AppStateStore(AppState(phase_ledger=PhaseLedger()))
        state = initial_loop_state((HumanMessage(content="task"),), tool_context_for(store))

        async def no_tools(calls, assistant_msgs, state, config):
            if False:
                yield

        async def no_compact(s, c):
            from app.query.state import CompactionResult
            return CompactionResult(compacted=False, messages=s.messages)

        deps = LoopDeps(call_model=model, compact=no_compact, uuid=lambda: "u1", now=lambda: 1000.0,
                        run_tools=no_tools, stop_hooks=tdd_phase_incomplete_hook, emit_event=lambda e: None)
        events = [ev async for ev in run_loop(state, build_run_config("t"), deps)]
        return events, store

    events, store = asyncio.run(go())
    terminated = events[-1]
    assert isinstance(terminated, Terminated) and terminated.reason == Terminal.STOP_HOOK_PREVENTED
    assert model.turns == []  # three model calls: two blocks, then the turn ended
    assert len([e for e in events if isinstance(e, HumanMessage) and e.additional_kwargs.get("is_meta")]) == 2
    assert store.get().phase_ledger == PhaseLedger()  # no evidence was created
