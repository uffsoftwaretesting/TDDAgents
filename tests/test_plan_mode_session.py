"""
A session in plan mode (`app/cli/print.py`, `app/query/stop_hooks.py`): an unplanned session
opens in plan mode with its configured mode as `prePlanMode`, the pool follows the mode, a
planning turn ends without the TDD hook, and an approved plan is developed in the same
conversation.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.cli.print import first_prompts, open_session_run, start_session
from app.query.config import build_run_config
from app.query.state import initial_loop_state
from app.query.stop_hooks import StopHookResult, build_stop_hooks_runner
from app.query.transitions import Terminal
from app.session.prompts import development_resume_prompt
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger
from app.tdd.plan import ItemStatus, TddPlan
from app.tool import tool_context_for
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.permissions.permission_setup import set_permission_mode
from app.utils.tool_result_storage import config_home
from tests.test_session_run import CALC_IMPL, CALC_TEST, call, cycle, options, scripted

CONFIG = build_run_config("plan-mode-test")
PLAN = "# Plan: calculator\n\n## Context\nAdd two numbers.\n\n## Implementation Plan\n1. add two numbers\n"


# ── the stop hooks ──────────────────────────────────────────────────────────


def stop_state(mode: PermissionMode) -> Any:
    store = AppStateStore(AppState(tool_permission_context=ToolPermissionContext(mode=mode),
                                   phase_ledger=PhaseLedger()))
    return initial_loop_state((HumanMessage(content="plan it"), AIMessage(content="Here is my analysis.")),
                              tool_context_for(store))


async def _stop(mode: PermissionMode) -> StopHookResult:
    return await build_stop_hooks_runner()(stop_state(mode), CONFIG)


def test_a_planning_turn_ends_without_the_tdd_hook():
    assert asyncio.run(_stop(PermissionMode.PLAN)) == StopHookResult()


def test_outside_plan_mode_the_tdd_hook_still_refuses():
    result = asyncio.run(_stop(PermissionMode.ACCEPT_EDITS))
    assert result.blocking_errors and not result.prevent_continuation


def test_a_context_without_permissions_is_not_planning():
    from app.query.stop_hooks import _in_plan_mode

    state = stop_state(PermissionMode.PLAN)
    state = dataclasses.replace(state, tool_context=object())
    assert _in_plan_mode(state) is False


# ── the first prompts ───────────────────────────────────────────────────────


def test_first_prompts():
    planned = TddPlan.from_texts(["add"])
    assert first_prompts(TddPlan(), "Build a calculator", None) == ("Build a calculator", None)
    assert first_prompts(TddPlan(), None, "my answer") == ("", "my answer")
    assert first_prompts(planned, None, None) == ("", development_resume_prompt(planned))
    assert first_prompts(planned, None, "go on") == ("", "go on")
    assert first_prompts(TddPlan(), None, None) == ("", None)


# ── opening the session ─────────────────────────────────────────────────────


def _open(tmp_path: Path, workspace: Any, plan: TddPlan, *, planning: bool, run_id: str,
          perm: ToolPermissionContext | None = None, deps: Any = None) -> Any:
    opts = options(tmp_path, deps or scripted([], []))
    if perm is not None:
        opts["permission_context"] = perm
    return open_session_run(run_id=run_id, plan=plan, workspace=workspace, planning=planning, **opts)


def test_an_unplanned_session_opens_in_plan_mode(python_workspace, tmp_path):
    async def go() -> None:
        async with _open(tmp_path, python_workspace, TddPlan(), planning=True, run_id="loop-p1") as run:
            context = run.store.get().tool_permission_context
            assert (context.mode, context.pre_plan_mode) == (PermissionMode.PLAN, PermissionMode.ACCEPT_EDITS)
            names = {t.name for t in run.ctx.tools}
            assert {"EnterPlanMode", "ExitPlanMode", "AskUserQuestion", "ReadFile"} <= names
            plans = run.ctx.session_plans
            # Upstream's default: `<config home>/plans` (the suite points the config home at a temp dir).
            assert plans is not None and plans.directory == config_home() / "plans"
            assert (Path(run.ctx.tdd_plan_path).parent / "plan-slug").read_text() == plans.slug

    asyncio.run(go())


def test_a_planned_or_non_planning_session_does_not(python_workspace, tmp_path):
    async def go(plan: TddPlan, planning: bool, run_id: str) -> PermissionMode:
        async with _open(tmp_path, python_workspace, plan, planning=planning, run_id=run_id) as run:
            mode: PermissionMode = run.store.get().tool_permission_context.mode
            return mode

    assert asyncio.run(go(TddPlan.from_texts(["add"]), True, "loop-p2")) == PermissionMode.ACCEPT_EDITS
    assert asyncio.run(go(TddPlan(), False, "loop-p3")) == PermissionMode.ACCEPT_EDITS


def test_the_pool_is_cached_per_phase_and_mode(python_workspace, tmp_path):
    async def go() -> None:
        async with _open(tmp_path, python_workspace, TddPlan(), planning=True, run_id="loop-p4") as run:
            refresh = run.ctx.refresh_tools
            planning = refresh()
            assert refresh() is planning
            run.store.update(lambda s: set_permission_mode(s, PermissionMode.ACCEPT_EDITS))
            developing = refresh()
            assert developing is not planning and refresh() is developing

    asyncio.run(go())


def test_in_coordinator_mode_the_analyst_keeps_the_whole_roster(python_workspace, tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_COORDINATOR_MODE", "1")

    async def go() -> None:
        async with _open(tmp_path, python_workspace, TddPlan(), planning=True, run_id="loop-p5") as run:
            assert {"ExitPlanMode", "ReadFile", "Agent"} <= {t.name for t in run.ctx.tools}
            run.store.update(lambda s: set_permission_mode(s, PermissionMode.ACCEPT_EDITS))
            developing = {t.name for t in run.ctx.refresh_tools()}
            assert "Agent" in developing and not developing & {"ReadFile", "ExitPlanMode", "EnterPlanMode"}

    asyncio.run(go())


def test_a_resumed_session_with_nothing_to_say_waits_for_the_user(python_workspace, tmp_path):
    seen: list[Any] = []

    async def go(run_id: str, turns: list[list[AIMessage]], request: str | None) -> Terminal | None:
        async with _open(tmp_path, python_workspace, TddPlan(), planning=True, run_id=run_id,
                         deps=scripted(turns, seen)) as run:
            return await start_session(run, request, None)

    assert asyncio.run(go("loop-p6", [[AIMessage(content="What should it do?")]], "Build it")) == Terminal.COMPLETED
    assert asyncio.run(go("loop-p6", [], None)) is None
    # Nothing to resume: a fresh session with no request has nothing to submit but its empty turn.
    assert asyncio.run(go("loop-p7", [[AIMessage(content="Hello.")]], None)) == Terminal.COMPLETED


# ── plan, approve, develop ──────────────────────────────────────────────────


def test_the_analyst_plans_the_user_approves_and_the_same_conversation_develops(python_workspace, tmp_path):
    from app.hooks.use_can_use_tool import (
        PermissionAnswer,
        build_interactive_can_use_tool,
    )

    seen: list[Any] = []
    plan_file: list[Path] = []

    async def write_plan(state: Any, config: Any) -> Any:
        plan_file.append(state.tool_context.session_plans.file_path())
        seen.append(state)
        yield call("WriteFile", "p1", file_path=str(plan_file[0]), content=PLAN)

    turns: list[Any] = [
        write_plan, [call("ExitPlanMode", "p2")],
        *cycle("tests/test_calc.py", CALC_TEST, CALC_IMPL, 1),
        [AIMessage(content="Done.")],
    ]
    base = scripted([], seen)

    async def model(state: Any, config: Any) -> Any:
        turn = turns.pop(0)
        if callable(turn):
            async for message in turn(state, config):
                yield message
            return
        seen.append(state)
        for message in turn:
            yield message

    async def approve(prompt: Any) -> PermissionAnswer:
        assert prompt.tool_name == "ExitPlanMode" and PLAN.rstrip() in prompt.message
        return PermissionAnswer("yes-accept-edits-keep-context")

    async def go() -> tuple[Terminal | None, Any]:
        opts = options(tmp_path, dataclasses.replace(base, call_model=model))
        opts["permission_context"] = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
        async with open_session_run(run_id="loop-p8", plan=TddPlan(), workspace=python_workspace, planning=True,
                                    can_use_tool=build_interactive_can_use_tool(approve), **opts) as run:
            terminal = await start_session(run, "Build a calculator", None)
            return terminal, run

    terminal, run = asyncio.run(go())
    assert terminal == Terminal.COMPLETED
    state = run.store.get()
    assert [(i.text, i.status) for i in state.tdd_plan.items] == [("add two numbers", ItemStatus.DONE)]
    assert state.tool_permission_context.mode == PermissionMode.ACCEPT_EDITS
    assert python_workspace.read_file("calculator.py") == CALC_IMPL
    assert plan_file[0].read_text(encoding="utf-8") == PLAN
    # The plan-mode reminder reached the first request; the exit reminder the first one after approval.
    first = " ".join(str(m.content) for m in seen[0].messages)
    assert "Plan mode is active." in first
    approved = next(m for m in seen[2].messages if isinstance(m, ToolMessage) and m.tool_call_id == "p2")
    assert "## Approved Plan:\n" in str(approved.content) and "Start with sub-requirement 1" in str(approved.content)
    assert any("## Exited Plan Mode" in str(m.content) for m in seen[2].messages)
