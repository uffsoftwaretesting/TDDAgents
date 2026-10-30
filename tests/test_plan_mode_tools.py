"""
`EnterPlanMode` and `ExitPlanMode` (`app/tools/{enter,exit}_plan_mode_tool/`), and the plan's
approval at the terminal (`app/hooks/use_can_use_tool.py::approve_plan`,
`app/components/permissions/permission_options.py::exit_plan_mode_options`).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolCall

from app.components.permissions.permission_options import exit_plan_mode_options
from app.hooks.use_can_use_tool import (
    EDIT_PLAN_COMMAND,
    PermissionAnswer,
    PermissionPrompt,
    build_interactive_can_use_tool,
    plan_approval_message,
)
from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.plan import ItemStatus, PlanItem, TddPlan, load_plan
from app.tool import tool_context_for
from app.tools.enter_plan_mode_tool.enter_plan_mode_tool import (
    AGENT_CONTEXT_ERROR,
    ENTERED_MESSAGE,
    INTERVIEW_INSTRUCTIONS,
    WORKFLOW_INSTRUCTIONS,
    build_enter_plan_mode_tool,
    enter_plan_mode_result_text,
    get_enter_plan_mode_tool_prompt,
)
from app.tools.exit_plan_mode_tool.exit_plan_mode_tool import (
    AGENT_APPROVED,
    EMPTY_PLAN_APPROVED,
    EXIT_PLAN_MODE_QUESTION,
    NOT_IN_PLAN_MODE,
    approved_plan_text,
    build_exit_plan_mode_tool,
    no_sub_requirements_message,
)
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    ToolPermissionContext,
)
from app.utils.messages import REJECT_MESSAGE, REJECT_MESSAGE_WITH_REASON_PREFIX
from app.utils.plan_mode_v2 import PLAN_MODE_INTERVIEW_PHASE_ENV
from app.utils.plans import SessionPlans
from tests.conftest import FakeWorkspace

REFERENCE = Path(__file__).resolve().parents[1] / "reference" / "claude-code" / "src" / "tools"
PLAN = "# Plan: calculator\n\n## Context\nAdd numbers.\n\n## Implementation Plan\n1. add two numbers\n2. subtract\n"


def planning_context(tmp_path: Path, *, mode: PermissionMode = PermissionMode.PLAN,
                     pre: PermissionMode | None = PermissionMode.ACCEPT_EDITS, plan: str | None = PLAN,
                     tdd_plan: TddPlan | None = None, interactive: bool = False) -> Any:
    perm = ToolPermissionContext(mode=mode, pre_plan_mode=pre, should_avoid_permission_prompts=not interactive)
    store = AppStateStore(AppState(tool_permission_context=perm, tdd_plan=tdd_plan or TddPlan(),
                                   phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    ctx = tool_context_for(store, workspace=FakeWorkspace())
    ctx.session_plans = SessionPlans(directory=tmp_path / "plans", slug="calm-planning-otter")
    ctx.tdd_plan_path = str(tmp_path / "tdd-plan.json")
    if plan is not None:
        (tmp_path / "plans").mkdir(parents=True, exist_ok=True)
        ctx.session_plans.file_path().write_text(plan, encoding="utf-8")
    return ctx


def run_call(ctx: Any, tool: Any, args: dict[str, Any] | None = None) -> Any:
    ctx.tools = (tool,)
    call = ToolCall(name=tool.name, args=args or {}, id="p1")
    return asyncio.run(run_tool_use(call, AIMessage(content=""), ctx, (tool,)))


async def _awaited(value: Any) -> Any:
    return await value if asyncio.iscoroutine(value) else value


def call_directly(tool: Any, args: dict[str, Any], ctx: Any) -> Any:
    return asyncio.run(tool.call(args, ctx))


# ── EnterPlanMode ───────────────────────────────────────────────────────────


def test_entering_plan_mode_stashes_the_mode(tmp_path: Path):
    ctx = planning_context(tmp_path, mode=PermissionMode.ACCEPT_EDITS, pre=None)
    result = call_directly(build_enter_plan_mode_tool(), {}, ctx)
    context = ctx.get_app_state().tool_permission_context
    assert (context.mode, context.pre_plan_mode) == (PermissionMode.PLAN, PermissionMode.ACCEPT_EDITS)
    assert result.content == enter_plan_mode_result_text()


def test_a_worker_cannot_enter_plan_mode(tmp_path: Path):
    ctx = planning_context(tmp_path, mode=PermissionMode.ACCEPT_EDITS)
    ctx.agent_id = "w1"
    with pytest.raises(RuntimeError, match=AGENT_CONTEXT_ERROR):
        call_directly(build_enter_plan_mode_tool(), {}, ctx)
    assert ctx.get_app_state().tool_permission_context.mode == PermissionMode.ACCEPT_EDITS


def test_enter_plan_mode_is_allowed_without_a_question(tmp_path: Path):
    ctx = planning_context(tmp_path, mode=PermissionMode.DEFAULT, pre=None)
    message = run_call(ctx, build_enter_plan_mode_tool()).message
    assert message.status == "success" and message.content.startswith(ENTERED_MESSAGE)


def test_the_enter_result_follows_the_interview_switch(monkeypatch):
    monkeypatch.delenv(PLAN_MODE_INTERVIEW_PHASE_ENV, raising=False)
    assert enter_plan_mode_result_text() == f"{ENTERED_MESSAGE}\n\n{WORKFLOW_INSTRUCTIONS}"
    assert "## What Happens in Plan Mode" in get_enter_plan_mode_tool_prompt()
    monkeypatch.setenv(PLAN_MODE_INTERVIEW_PHASE_ENV, "1")
    assert enter_plan_mode_result_text() == f"{ENTERED_MESSAGE}\n\n{INTERVIEW_INSTRUCTIONS}"
    assert "## What Happens in Plan Mode" not in get_enter_plan_mode_tool_prompt()
    assert "{{" not in get_enter_plan_mode_tool_prompt()


def _ts_template(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    match = re.search(rf"{name}[^`]*`(.*?)`", source, re.DOTALL)
    assert match is not None, name
    return match.group(1)


@pytest.mark.skipif(not (REFERENCE / "EnterPlanModeTool" / "prompt.ts").exists(), reason="reference snapshot absent")
def test_the_enter_prompt_is_upstreams(monkeypatch):
    monkeypatch.delenv(PLAN_MODE_INTERVIEW_PHASE_ENV, raising=False)
    prompt_ts = REFERENCE / "EnterPlanModeTool" / "prompt.ts"
    upstream = _ts_template(prompt_ts, "function getEnterPlanModeToolPromptExternal")
    what = _ts_template(REFERENCE / "EnterPlanModeTool" / "prompt.ts", "const WHAT_HAPPENS_SECTION =")
    expected = (upstream.replace("${whatHappens}", what).replace("${ASK_USER_QUESTION_TOOL_NAME}", "AskUserQuestion")
                .replace("${EXIT_PLAN_MODE_TOOL_NAME}", "ExitPlanMode")
                # Our tool names (CLAUDE.md: upstream's text with our tool names).
                .replace("Glob, Grep, and Read tools", "Glob, Grep, and ReadFile tools"))
    assert "${" not in expected
    assert get_enter_plan_mode_tool_prompt().strip() == expected.strip()


# ── ExitPlanMode: validation and the ask ────────────────────────────────────


def test_outside_plan_mode_it_is_refused(tmp_path: Path):
    ctx = planning_context(tmp_path, mode=PermissionMode.ACCEPT_EDITS)
    result = build_exit_plan_mode_tool().validate_input({}, ctx)
    assert (result.valid, result.message, result.error_code) == (False, NOT_IN_PLAN_MODE, 1)


@pytest.mark.parametrize("plan", [None, "# Plan\n\n## Context\nno items\n"])
def test_a_plan_without_sub_requirements_is_refused(tmp_path: Path, plan: str | None):
    ctx = planning_context(tmp_path, plan=plan)
    result = build_exit_plan_mode_tool().validate_input({}, ctx)
    expected = no_sub_requirements_message(str(ctx.session_plans.file_path()))
    assert (result.valid, result.message, result.error_code) == (False, expected, 2)
    assert expected == (
        f"The plan file at {ctx.session_plans.file_path()} has no numbered sub-requirements under a "
        '"Implementation Plan" heading. TDDAgents runs each numbered item of that section as one '
        "sub-requirement, so the plan cannot be approved without it.")


def test_a_plan_with_sub_requirements_validates(tmp_path: Path):
    assert build_exit_plan_mode_tool().validate_input({}, planning_context(tmp_path)).valid
    worker = planning_context(tmp_path, plan=None)
    worker.agent_id = "w1"
    assert build_exit_plan_mode_tool().validate_input({}, worker).valid


def test_it_always_asks_and_needs_a_person(tmp_path: Path):
    tool = build_exit_plan_mode_tool()
    result = asyncio.run(_awaited(tool.check_permissions({"allowedPrompts": []}, planning_context(tmp_path))))
    assert (result.behavior, result.message, result.updated_input) == (
        PermissionBehavior.ASK, EXIT_PLAN_MODE_QUESTION, {"allowedPrompts": []})
    assert tool.requires_user_interaction() is True


def test_with_nobody_to_ask_it_is_denied_and_nothing_changes(tmp_path: Path):
    """Upstream's `-p`: the plan is never approved, so the session stays in plan mode."""
    ctx = planning_context(tmp_path, pre=PermissionMode.BYPASS_PERMISSIONS)
    message = run_call(ctx, build_exit_plan_mode_tool()).message
    assert message.status == "error" and message.additional_kwargs["permission_behavior"] == "deny"
    assert ctx.get_app_state().tool_permission_context.mode == PermissionMode.PLAN
    assert ctx.get_app_state().tdd_plan.items == ()


# ── ExitPlanMode: the call ──────────────────────────────────────────────────


def test_approval_restores_the_mode_and_installs_the_sub_requirements(tmp_path: Path):
    from app.session.prompts import development_approved_prompt

    ctx = planning_context(tmp_path)
    result = call_directly(build_exit_plan_mode_tool(), {}, ctx)
    state = ctx.get_app_state()
    assert (state.tool_permission_context.mode, state.tool_permission_context.pre_plan_mode) == (
        PermissionMode.ACCEPT_EDITS, None)
    assert state.has_exited_plan_mode and state.needs_plan_mode_exit_attachment
    assert [i.text for i in state.tdd_plan.items] == ["add two numbers", "subtract"]
    # A new current item: the ledger restarts in RED.
    assert state.phase_ledger == PhaseLedger()
    assert load_plan(Path(ctx.tdd_plan_path)) == state.tdd_plan
    file_path = str(ctx.session_plans.file_path())
    assert result.content == (approved_plan_text(PLAN, file_path, is_agent=False, edited=False) + "\n\n"
                              + development_approved_prompt(state.tdd_plan))
    assert result.metadata == {"plan": PLAN, "filePath": file_path, "isAgent": False, "planWasEdited": False,
                               "subRequirements": ["add two numbers", "subtract"]}


def test_the_approved_text_is_upstreams(tmp_path: Path):
    assert approved_plan_text(PLAN, "/p.md", is_agent=False, edited=False) == (
        "User has approved your plan. You can now start coding. Start with updating your todo list if applicable\n\n"
        "Your plan has been saved to: /p.md\nYou can refer back to it if needed during implementation.\n\n"
        f"## Approved Plan:\n{PLAN}")
    assert "## Approved Plan (edited by user):\n" in approved_plan_text(PLAN, "/p.md", is_agent=False, edited=True)
    assert approved_plan_text(PLAN, "/p.md", is_agent=True, edited=False) == AGENT_APPROVED
    assert approved_plan_text("  ", "/p.md", is_agent=False, edited=False) == EMPTY_PLAN_APPROVED
    assert approved_plan_text(None, "/p.md", is_agent=False, edited=False) == EMPTY_PLAN_APPROVED


def test_an_edited_plan_is_written_back_and_labelled(tmp_path: Path):
    ctx = planning_context(tmp_path)
    edited = PLAN.replace("2. subtract", "2. subtract\n3. multiply")
    result = call_directly(build_exit_plan_mode_tool(), {"plan": edited}, ctx)
    assert ctx.session_plans.read() == edited
    assert "## Approved Plan (edited by user):\n" in result.content
    assert [i.text for i in ctx.get_app_state().tdd_plan.items] == ["add two numbers", "subtract", "multiply"]
    assert result.metadata["planWasEdited"] is True


def test_re_approval_keeps_the_done_items(tmp_path: Path):
    done = TddPlan(items=(PlanItem(id="1", text="add two numbers", status=ItemStatus.DONE),
                          PlanItem(id="2", text="old pending")))
    ctx = planning_context(tmp_path, tdd_plan=done,
                           plan="## Implementation Plan\n1. subtract\n2. multiply\n")
    call_directly(build_exit_plan_mode_tool(), {}, ctx)
    items = ctx.get_app_state().tdd_plan.items
    assert [(i.text, i.status) for i in items] == [
        ("add two numbers", ItemStatus.DONE), ("subtract", ItemStatus.PENDING), ("multiply", ItemStatus.PENDING)]


def test_a_mode_the_user_already_chose_is_kept(tmp_path: Path):
    """The approval's "Yes" sets the mode first (upstream's `setMode`); the call does not undo it."""
    ctx = planning_context(tmp_path, mode=PermissionMode.DEFAULT, pre=None)
    call_directly(build_exit_plan_mode_tool(), {}, ctx)
    assert ctx.get_app_state().tool_permission_context.mode == PermissionMode.DEFAULT


def test_no_pre_plan_mode_restores_default(tmp_path: Path):
    ctx = planning_context(tmp_path, pre=None)
    call_directly(build_exit_plan_mode_tool(), {}, ctx)
    assert ctx.get_app_state().tool_permission_context.mode == PermissionMode.DEFAULT


def test_the_call_refuses_a_plan_without_items_and_a_session_without_plans(tmp_path: Path):
    ctx = planning_context(tmp_path, plan="## Context\n")
    with pytest.raises(RuntimeError, match="has no numbered sub-requirements"):
        call_directly(build_exit_plan_mode_tool(), {}, ctx)
    assert ctx.get_app_state().tool_permission_context.mode == PermissionMode.PLAN
    ctx.session_plans = None
    with pytest.raises(RuntimeError, match="This session has no plan file."):
        call_directly(build_exit_plan_mode_tool(), {}, ctx)


def test_a_worker_approval_installs_nothing(tmp_path: Path):
    ctx = planning_context(tmp_path, plan=None)
    ctx.agent_id = "w1"
    ctx.session_plans.directory.mkdir(parents=True, exist_ok=True)
    ctx.session_plans.file_path("w1").write_text("worker plan", encoding="utf-8")
    result = call_directly(build_exit_plan_mode_tool(), {}, ctx)
    assert result.content == AGENT_APPROVED
    assert result.metadata["plan"] == "worker plan" and result.metadata["subRequirements"] == []
    assert ctx.get_app_state().tdd_plan.items == ()


@pytest.mark.skipif(not (REFERENCE / "ExitPlanModeTool" / "ExitPlanModeV2Tool.ts").exists(),
                    reason="reference snapshot absent")
def test_the_messages_are_upstreams():
    source = (REFERENCE / "ExitPlanModeTool" / "ExitPlanModeV2Tool.ts").read_text(encoding="utf-8")
    for text in (NOT_IN_PLAN_MODE, AGENT_APPROVED, EMPTY_PLAN_APPROVED, EXIT_PLAN_MODE_QUESTION,
                 ("User has approved your plan. You can now start coding. Start with updating your todo list if "
                  "applicable"), "You can refer back to it if needed during implementation."):
        assert text in source.replace("\\n", "\n"), text


# ── the approval at the terminal ────────────────────────────────────────────


def test_the_options():
    plain = exit_plan_mode_options(ToolPermissionContext())
    assert [(o.value, o.label, o.reject) for o in plain] == [
        ("yes-accept-edits-keep-context", "Yes, auto-accept edits", False),
        ("yes-default-keep-context", "Yes, manually approve edits", False),
        ("no", "No, keep planning", True)]
    assert [u.mode for u in plain[0].updates] == [PermissionMode.ACCEPT_EDITS]
    assert [u.mode for u in plain[1].updates] == [PermissionMode.DEFAULT]
    bypass = exit_plan_mode_options(ToolPermissionContext(is_bypass_permissions_mode_available=True))
    assert (bypass[0].label, [u.mode for u in bypass[0].updates]) == (
        "Yes, and bypass permissions", [PermissionMode.BYPASS_PERMISSIONS])


def test_the_approval_message():
    assert plan_approval_message("# P\n\n") == (
        "Here is the plan:\n\n# P\n\nWould you like to proceed?\n(Type /edit to edit the plan in your editor.)")
    assert plan_approval_message("# P", "No editor.").endswith("editor.)\nNo editor.")


def interactive(tmp_path: Path, answers: list[PermissionAnswer], edits: list[tuple[str | None, str]] | None = None,
                **kw: Any) -> tuple[Any, list[PermissionPrompt], list[str]]:
    prompts: list[PermissionPrompt] = []
    edited_from: list[str] = []
    queue = list(answers)
    edit_queue = list(edits or [])

    async def ask(prompt: PermissionPrompt) -> PermissionAnswer:
        prompts.append(prompt)
        return queue.pop(0)

    def edit_plan(text: str) -> tuple[str | None, str]:
        edited_from.append(text)
        return edit_queue.pop(0)

    ctx = planning_context(tmp_path, interactive=True, **kw)
    ctx.can_use_tool = build_interactive_can_use_tool(ask, edit_plan=edit_plan)
    return ctx, prompts, edited_from


@pytest.mark.parametrize(("value", "mode"), [("yes-accept-edits-keep-context", PermissionMode.ACCEPT_EDITS),
                                             ("yes-default-keep-context", PermissionMode.DEFAULT)])
def test_yes_sets_the_mode_and_development_starts(tmp_path: Path, value: str, mode: PermissionMode):
    ctx, prompts, _ = interactive(tmp_path, [PermissionAnswer(value)], pre=PermissionMode.PLAN)
    message = run_call(ctx, build_exit_plan_mode_tool()).message
    assert message.status == "success" and "## Approved Plan:\n" in message.content
    (prompt,) = prompts
    assert prompt.tool_name == "ExitPlanMode" and prompt.message == plan_approval_message(PLAN)
    assert [o.value for o in prompt.options][2] == "no"
    state = ctx.get_app_state()
    assert state.tool_permission_context.mode == mode and state.has_exited_plan_mode
    assert len(state.tdd_plan.items) == 2


def test_keep_planning_with_feedback_tells_the_model(tmp_path: Path):
    ctx, _, _ = interactive(tmp_path, [PermissionAnswer("no", "  Split item 1.  ")])
    message = run_call(ctx, build_exit_plan_mode_tool()).message
    assert (message.status, message.content) == ("error", f"{REJECT_MESSAGE_WITH_REASON_PREFIX}Split item 1.")
    assert ctx.cancel.cancelled is False
    assert ctx.get_app_state().tool_permission_context.mode == PermissionMode.PLAN
    assert ctx.get_app_state().tdd_plan.items == ()


def test_a_plain_no_ends_the_turn(tmp_path: Path):
    ctx, _, _ = interactive(tmp_path, [PermissionAnswer("no")])
    message = run_call(ctx, build_exit_plan_mode_tool()).message
    assert (message.status, message.content) == ("error", REJECT_MESSAGE)
    assert ctx.cancel.cancelled is True


def test_edit_opens_the_editor_asks_again_and_approves_the_edit(tmp_path: Path):
    edited = PLAN + "3. multiply\n"
    ctx, prompts, edited_from = interactive(
        tmp_path,
        [PermissionAnswer("no", EDIT_PLAN_COMMAND), PermissionAnswer("no", EDIT_PLAN_COMMAND),
         PermissionAnswer("yes-default-keep-context")],
        edits=[(edited, ""), (None, "The editor exited with an error.")])
    message = run_call(ctx, build_exit_plan_mode_tool()).message
    assert edited_from == [PLAN, edited]
    assert [p.message for p in prompts] == [
        plan_approval_message(PLAN), plan_approval_message(edited),
        plan_approval_message(edited, "The editor exited with an error.")]
    assert "## Approved Plan (edited by user):\n" in message.content
    assert ctx.session_plans.read() == edited
    assert [i.text for i in ctx.get_app_state().tdd_plan.items][-1] == "multiply"


def test_an_unknown_answer_is_a_no(tmp_path: Path):
    ctx, _, _ = interactive(tmp_path, [PermissionAnswer("maybe")])
    assert run_call(ctx, build_exit_plan_mode_tool()).message.content == REJECT_MESSAGE
