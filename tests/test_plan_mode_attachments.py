"""
The plan-mode attachments (`app/utils/attachments.py`): `plan_mode` (full / sparse / sub-agent,
every 5 human turns), `plan_mode_reentry` and `plan_mode_exit`, ported from
`reference/claude-code/src/utils/attachments.ts` (`getPlanModeAttachments`,
`getPlanModeExitAttachment`) and `utils/messages.ts` (`getPlanModeInstructions` and its texts).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.state.app_state_store import AppState, AppStateStore
from app.tool import tool_context_for
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.attachments import (
    PLAN_MODE_ATTACHMENT_CONFIG,
    PLAN_PHASE4_CONTROL,
    Attachment,
    create_attachment_messages,
    get_attachments,
    get_plan_mode_attachments,
    get_plan_mode_exit_attachment,
    normalize_attachment_for_api,
    plan_mode_exit_text,
    plan_mode_instructions,
    plan_mode_reentry_text,
)
from app.utils.plan_mode_v2 import (
    PLAN_MODE_INTERVIEW_PHASE_ENV,
    PLAN_V2_AGENT_COUNT_ENV,
)
from app.utils.plans import SessionPlans

MESSAGES_TS = Path(__file__).resolve().parents[1] / "reference" / "claude-code" / "src" / "utils" / "messages.ts"
PATH = "/plans/calm-planning-otter.md"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv(PLAN_MODE_INTERVIEW_PHASE_ENV, raising=False)
    monkeypatch.delenv(PLAN_V2_AGENT_COUNT_ENV, raising=False)


def context(tmp_path: Path, mode: PermissionMode = PermissionMode.PLAN, *, plan: str | None = None,
            agent_id: str | None = None, **state: Any) -> Any:
    store = AppStateStore(AppState(tool_permission_context=ToolPermissionContext(mode=mode), **state))
    ctx = tool_context_for(store, agent_id=agent_id)
    ctx.session_plans = SessionPlans(directory=tmp_path, slug="s")
    if plan is not None:
        ctx.session_plans.file_path(agent_id).write_text(plan, encoding="utf-8")
    return ctx


def human(text: str = "go") -> HumanMessage:
    return HumanMessage(content=text)


def reminder(kind: str = "plan_mode", **data: Any) -> Any:
    (message,) = create_attachment_messages(Attachment(kind, {"reminderType": "full", "planFilePath": PATH,
                                                              "planExists": False, **data}))
    return message


# ── the cadence ─────────────────────────────────────────────────────────────


def test_the_config_is_upstreams():
    assert (PLAN_MODE_ATTACHMENT_CONFIG.TURNS_BETWEEN_ATTACHMENTS,
            PLAN_MODE_ATTACHMENT_CONFIG.FULL_REMINDER_EVERY_N_ATTACHMENTS) == (5, 5)


def test_only_in_plan_mode_and_only_with_a_plan_file(tmp_path: Path):
    assert get_plan_mode_attachments([], context(tmp_path, PermissionMode.ACCEPT_EDITS)) == []
    ctx = context(tmp_path)
    ctx.session_plans = None
    assert get_plan_mode_attachments([], ctx) == []


def test_the_first_reminder_is_full(tmp_path: Path):
    ctx = context(tmp_path)
    assert get_plan_mode_attachments([], ctx) == [Attachment("plan_mode", {
        "reminderType": "full", "isSubAgent": False, "planFilePath": str(tmp_path / "s.md"), "planExists": False})]
    with_plan = context(tmp_path, plan="# P")
    assert get_plan_mode_attachments([human()], with_plan)[0].data["planExists"] is True


def test_then_every_five_human_turns(tmp_path: Path):
    ctx = context(tmp_path)
    messages: list[Any] = [human(), reminder()]
    for _ in range(4):
        messages += [human(), AIMessage(content="thinking")]
        assert get_plan_mode_attachments(messages, ctx) == []
    # Meta messages are not human turns.
    messages.append(HumanMessage(content="meta", additional_kwargs={"is_meta": True}))
    assert get_plan_mode_attachments(messages, ctx) == []
    messages.append(human())
    (attachment,) = get_plan_mode_attachments(messages, ctx)
    assert attachment.data["reminderType"] == "sparse"


def test_every_fifth_reminder_is_full_again(tmp_path: Path):
    ctx = context(tmp_path)
    messages: list[Any] = []
    kinds = []
    for _ in range(6):
        (attachment,) = get_plan_mode_attachments(messages, ctx)
        kinds.append(attachment.data["reminderType"])
        messages += [reminder(), *[human() for _ in range(5)]]
    assert kinds == ["full", "sparse", "sparse", "sparse", "sparse", "full"]


def test_the_count_restarts_after_an_exit(tmp_path: Path):
    ctx = context(tmp_path)
    messages = [reminder(), *[human() for _ in range(5)], reminder("plan_mode_exit"), *[human() for _ in range(5)]]
    assert get_plan_mode_attachments(messages, ctx)[0].data["reminderType"] == "full"


def test_a_re_entry_reminder_counts_as_the_last_reminder(tmp_path: Path):
    ctx = context(tmp_path)
    assert get_plan_mode_attachments([reminder("plan_mode_reentry"), human()], ctx) == []


def test_re_entering_with_an_existing_plan(tmp_path: Path):
    ctx = context(tmp_path, plan="# old plan", has_exited_plan_mode=True)
    reentry, plan_mode = get_plan_mode_attachments([], ctx)
    assert reentry == Attachment("plan_mode_reentry", {"planFilePath": str(tmp_path / "s.md")})
    assert plan_mode.type == "plan_mode"
    assert ctx.get_app_state().has_exited_plan_mode is False
    # Without a plan file there is nothing to re-enter, and the flag stays.
    fresh = context(tmp_path / "x", has_exited_plan_mode=True)
    (tmp_path / "x").mkdir()
    assert [a.type for a in get_plan_mode_attachments([], fresh)] == ["plan_mode"]
    assert fresh.get_app_state().has_exited_plan_mode is True


def test_a_worker_gets_the_sub_agent_reminder_for_its_own_file(tmp_path: Path):
    ctx = context(tmp_path, agent_id="w1")
    (attachment,) = get_plan_mode_attachments([], ctx)
    assert attachment.data["isSubAgent"] is True
    assert attachment.data["planFilePath"] == str(tmp_path / "s-agent-w1.md")


# ── the exit attachment ─────────────────────────────────────────────────────


def test_the_exit_attachment_is_sent_once_after_leaving(tmp_path: Path):
    ctx = context(tmp_path, PermissionMode.ACCEPT_EDITS, plan="# P", needs_plan_mode_exit_attachment=True)
    assert get_plan_mode_exit_attachment([], ctx) == [
        Attachment("plan_mode_exit", {"planFilePath": str(tmp_path / "s.md"), "planExists": True})]
    assert ctx.get_app_state().needs_plan_mode_exit_attachment is False
    assert get_plan_mode_exit_attachment([], ctx) == []


def test_no_exit_attachment_while_back_in_plan_mode(tmp_path: Path):
    ctx = context(tmp_path, needs_plan_mode_exit_attachment=True)
    assert get_plan_mode_exit_attachment([], ctx) == []
    assert ctx.get_app_state().needs_plan_mode_exit_attachment is False


def test_no_exit_attachment_without_the_flag_or_a_plan(tmp_path: Path):
    assert get_plan_mode_exit_attachment([], context(tmp_path, PermissionMode.DEFAULT)) == []
    ctx = context(tmp_path, PermissionMode.DEFAULT, needs_plan_mode_exit_attachment=True)
    ctx.session_plans = None
    assert get_plan_mode_exit_attachment([], ctx) == []
    assert get_plan_mode_exit_attachment([], object()) == []


def test_both_sources_run_through_get_attachments(tmp_path: Path):
    ctx = context(tmp_path)
    kinds = [a.type for a in asyncio.run(get_attachments(ctx, [], user_input="plan it"))]
    assert "plan_mode" in kinds
    left = context(tmp_path, PermissionMode.ACCEPT_EDITS, needs_plan_mode_exit_attachment=True)
    assert "plan_mode_exit" in [a.type for a in asyncio.run(get_attachments(left, [human()]))]


# ── the texts ───────────────────────────────────────────────────────────────


def test_the_dispatch():
    full = {"reminderType": "full", "planFilePath": PATH, "planExists": False}
    assert plan_mode_instructions(full).startswith("Plan mode is active.")
    assert plan_mode_instructions({**full, "reminderType": "sparse"}).startswith("Plan mode still active")
    assert "Instead, you should:" in plan_mode_instructions({**full, "isSubAgent": True})
    assert normalize_attachment_for_api(Attachment("plan_mode", full)) == [plan_mode_instructions(full)]
    assert normalize_attachment_for_api(Attachment("plan_mode_reentry", {"planFilePath": PATH})) == [
        plan_mode_reentry_text({"planFilePath": PATH})]
    assert normalize_attachment_for_api(Attachment("plan_mode_exit", {"planFilePath": PATH})) == [
        plan_mode_exit_text({"planFilePath": PATH})]


def test_the_exit_text():
    assert plan_mode_exit_text({"planFilePath": PATH, "planExists": False}) == (
        "## Exited Plan Mode\n\nYou have exited plan mode. You can now make edits, run tools, and take actions.")
    assert plan_mode_exit_text({"planFilePath": PATH, "planExists": True}).endswith(
        f"take actions. The plan file is located at {PATH} if you need to reference it.")


def test_the_agent_count_switch(monkeypatch):
    full = {"reminderType": "full", "planFilePath": PATH, "planExists": False}
    assert "**Multiple agents**" not in plan_mode_instructions(full)
    assert "You can launch up to 1 agent(s) in parallel." in plan_mode_instructions(full)
    monkeypatch.setenv(PLAN_V2_AGENT_COUNT_ENV, "3")
    text = plan_mode_instructions(full)
    assert "You can launch up to 3 agent(s) in parallel." in text
    assert "- **Multiple agents**: Use up to 3 agents for complex tasks" in text


def test_the_interview_switch(monkeypatch):
    full = {"reminderType": "full", "planFilePath": PATH, "planExists": True}
    monkeypatch.setenv(PLAN_MODE_INTERVIEW_PHASE_ENV, "1")
    assert "## Iterative Planning Workflow" in plan_mode_instructions(full)
    assert "Follow iterative workflow" in plan_mode_instructions({**full, "reminderType": "sparse"})


# ── pinned against upstream's source ────────────────────────────────────────

#: Upstream's interpolations, with this build's tool names and the external defaults.
NAMES = {
    "FileEditTool.name": "Edit", "FileWriteTool.name": "WriteFile", "EXPLORE_AGENT.agentType": "Explore",
    "PLAN_AGENT.agentType": "Plan", "ASK_USER_QUESTION_TOOL_NAME": "AskUserQuestion",
    "ExitPlanModeV2Tool.name": "ExitPlanMode", "getPlanPhase4Section()": PLAN_PHASE4_CONTROL,
    "getReadOnlyToolNames()": "ReadFile, Glob, Grep",
}


def _upstream(function: str, values: dict[str, str]) -> str:
    """`function`'s `content` template from `messages.ts`, rendered with `values`."""
    source = MESSAGES_TS.read_text(encoding="utf-8")
    body = source[source.index(f"function {function}("):]
    template = re.search(r"const content = `(.*?)`\n\n  return wrapMessagesInSystemReminder", body, re.DOTALL)
    assert template is not None, function
    text = template.group(1)
    text = re.sub(r"\$\{\n  agentCount > 1\n    \? `(.*?)`\n    : ''\n\}",
                  lambda m: m.group(1) if int(values["agentCount"]) > 1 else "", text, flags=re.DOTALL)
    text = re.sub(r"\$\{areExplorePlanAgentsEnabled\(\) \? `(.*?)` : ''\}", lambda m: m.group(1), text, flags=re.DOTALL)
    text = re.sub(r"\$\{([^}]+)\}", lambda m: {**NAMES, **values}[m.group(1)], text)
    return re.sub(r"\$\{([^}]+)\}", lambda m: {**NAMES, **values}[m.group(1)], text)


def _plan_file_info(exists: bool, suffix: str = "") -> str:
    return (f"A plan file already exists at {PATH}. You can read it and make incremental edits using the Edit "
            f"tool{suffix}." if exists else
            f"No plan file exists yet. You should create your plan at {PATH} using the WriteFile tool{suffix}.")


needs_reference = pytest.mark.skipif(not MESSAGES_TS.exists(), reason="reference snapshot absent")


@needs_reference
@pytest.mark.parametrize("agents", ["1", "3"])
@pytest.mark.parametrize("exists", [False, True])
def test_the_five_phase_text_is_upstreams(monkeypatch, agents: str, exists: bool):
    monkeypatch.setenv(PLAN_V2_AGENT_COUNT_ENV, agents)
    expected = _upstream("getPlanModeV2Instructions", {
        "planFileInfo": _plan_file_info(exists), "agentCount": agents, "exploreAgentCount": "3"})
    assert plan_mode_instructions({"reminderType": "full", "planFilePath": PATH, "planExists": exists}) == expected


@needs_reference
def test_the_phase4_control_is_upstreams():
    source = MESSAGES_TS.read_text(encoding="utf-8")
    match = re.search(r"export const PLAN_PHASE4_CONTROL = `(.*?)`", source, re.DOTALL)
    assert match is not None and PLAN_PHASE4_CONTROL == match.group(1)


@needs_reference
@pytest.mark.parametrize("exists", [False, True])
def test_the_interview_text_is_upstreams(monkeypatch, exists: bool):
    monkeypatch.setenv(PLAN_MODE_INTERVIEW_PHASE_ENV, "1")
    expected = _upstream("getPlanModeInterviewInstructions", {"planFileInfo": _plan_file_info(exists)})
    assert plan_mode_instructions({"reminderType": "full", "planFilePath": PATH, "planExists": exists}) == expected


@needs_reference
@pytest.mark.parametrize("interview", [False, True])
def test_the_sparse_text_is_upstreams(monkeypatch, interview: bool):
    if interview:
        monkeypatch.setenv(PLAN_MODE_INTERVIEW_PHASE_ENV, "1")
    workflow = ("Follow iterative workflow: explore codebase, interview user, write to plan incrementally."
                if interview else "Follow 5-phase workflow.")
    expected = _upstream("getPlanModeV2SparseInstructions", {
        "attachment.planFilePath": PATH, "workflowDescription": workflow})
    assert plan_mode_instructions({"reminderType": "sparse", "planFilePath": PATH}) == expected


@needs_reference
@pytest.mark.parametrize("exists", [False, True])
def test_the_sub_agent_text_is_upstreams(exists: bool):
    expected = _upstream("getPlanModeV2SubAgentInstructions", {"planFileInfo": _plan_file_info(exists,
                                                                                               " if you need to")})
    assert plan_mode_instructions({"isSubAgent": True, "planFilePath": PATH, "planExists": exists}) == expected


@needs_reference
def test_the_reentry_and_exit_texts_are_upstreams():
    source = MESSAGES_TS.read_text(encoding="utf-8")
    reentry = re.search(r"case 'plan_mode_reentry': \{\n      const content = `(.*?)`", source, re.DOTALL)
    assert reentry is not None
    assert plan_mode_reentry_text({"planFilePath": PATH}) == reentry.group(1).replace(
        "${attachment.planFilePath}", PATH).replace("${ExitPlanModeV2Tool.name}", "ExitPlanMode")
    exit_ = re.search(r"case 'plan_mode_exit': \{.*?const content = `(.*?)`", source, re.DOTALL)
    assert exit_ is not None
    reference = f" The plan file is located at {PATH} if you need to reference it."
    assert plan_mode_exit_text({"planFilePath": PATH, "planExists": True}) == exit_.group(1).replace(
        "${planReference}", reference)


def test_create_attachment_messages_wraps_them_as_meta():
    (message,) = create_attachment_messages(Attachment("plan_mode_exit", {"planFilePath": PATH}))
    assert message.additional_kwargs["is_meta"] is True
    assert "## Exited Plan Mode" in message.content


# ── after a compaction (`compact.ts`) ───────────────────────────────────────


def test_after_a_compaction_the_plan_file_and_plan_mode_come_back(tmp_path: Path):
    from app.services.compact.compact import compact_conversation

    ctx = context(tmp_path, plan="# The plan")

    async def answer() -> AIMessage:
        return AIMessage(content="<summary>planning so far</summary>")

    def summarize(messages: Any, summary_request: Any, context: Any, *, max_output_tokens: int) -> Any:
        return answer()

    def tdd(context_: Any) -> list[Any]:
        return [HumanMessage(content="tdd state", additional_kwargs={"is_meta": True})]

    result = asyncio.run(compact_conversation([human("plan it"), AIMessage(content="exploring", id="a1")], ctx,
                                              summarizer=summarize, trigger="auto", post_compact_attachments=tdd,
                                              model="m"))
    texts = [str(m.content) for m in result.attachments]
    path = tmp_path / "s.md"
    assert texts == [
        ("<system-reminder>\nA plan file exists from plan mode at: "
         f"{path}\n\nPlan contents:\n\n# The plan\n\nIf this plan is relevant to the current work and not already "
         "complete, continue working on it.\n</system-reminder>"),
        (f"<system-reminder>\n{plan_mode_instructions({'planFilePath': str(path), 'planExists': True})}\n"
         "</system-reminder>"),
        "tdd state",
    ]


def test_the_compaction_helpers_without_a_plan_or_outside_plan_mode(tmp_path: Path):
    from app.services.compact.compact import (
        create_plan_attachment_if_needed,
        create_plan_mode_attachment_if_needed,
    )

    empty = context(tmp_path)
    assert create_plan_attachment_if_needed(empty) == []
    (only_mode,) = create_plan_mode_attachment_if_needed(empty)
    assert "No plan file exists yet." in str(only_mode.content)
    developing = context(tmp_path, PermissionMode.ACCEPT_EDITS, plan="# P")
    assert create_plan_mode_attachment_if_needed(developing) == []
    assert len(create_plan_attachment_if_needed(developing)) == 1
    developing.session_plans = None
    assert create_plan_attachment_if_needed(developing) == []
    assert create_plan_mode_attachment_if_needed(developing) == []
    worker = context(tmp_path, agent_id="w1", plan="# W")
    (mode,) = create_plan_mode_attachment_if_needed(worker)
    assert "Instead, you should:" in str(mode.content) and "s-agent-w1.md" in str(mode.content)
    (ref,) = create_plan_attachment_if_needed(worker)
    assert "# W" in str(ref.content)


@needs_reference
def test_the_plan_file_reference_text_is_upstreams():
    source = MESSAGES_TS.read_text(encoding="utf-8")
    match = re.search(r"case 'plan_file_reference': \{.*?content: `(.*?)`", source, re.DOTALL)
    assert match is not None
    expected = (match.group(1).replace("\\n", "\n").replace("${attachment.planFilePath}", PATH)
                .replace("${attachment.planContent}", "# P"))
    assert normalize_attachment_for_api(Attachment("plan_file_reference", {"planFilePath": PATH,
                                                                           "planContent": "# P"})) == [expected]
