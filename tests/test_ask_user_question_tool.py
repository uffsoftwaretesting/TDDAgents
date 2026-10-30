"""
`AskUserQuestion` (`app/tools/ask_user_question_tool/`): upstream's tool, its question in the REPL,
its denial with nobody to ask, and the stop that waits for the user's answer.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolCall, ToolMessage

from app.cli.print import open_session_run, run_headless, session_run_id
from app.hooks.use_can_use_tool import (
    PermissionAnswer,
    PermissionPrompt,
    build_interactive_can_use_tool,
)
from app.main import waiting_text
from app.query.config import build_run_config
from app.query.state import initial_loop_state
from app.query.stop_hooks import StopHookResult, build_stop_hooks_runner
from app.query.transitions import Terminal
from app.screens.repl import Repl, parse_question_answer, render_question
from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger
from app.tdd.plan import ItemStatus, TddPlan, plan_path, save_plan
from app.tdd.stop_hook import ASK_THE_USER, tdd_phase_incomplete_hook
from app.tool import tool_context_for
from app.tools.ask_user_question_tool.ask_user_question_tool import (
    ASK_USER_QUESTION_INPUT_SCHEMA,
    UNIQUENESS_MESSAGE,
    answers_text,
    awaiting_user_answer,
    build_ask_user_question_tool,
    questions_are_unique,
    render_questions,
)
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.messages import REJECT_MESSAGE, auto_reject_message
from app.utils.permissions.yolo_classifier import to_auto_classifier_input
from tests.conftest import FakeWorkspace
from tests.test_repl import FakeIO, until

QUESTION: dict[str, Any] = {
    "question": "Which size should the performance test use?",
    "header": "Perf size",
    "options": [
        {"label": "Moderate (Recommended)", "description": "400k characters, 5 s budget"},
        {"label": "Full", "description": "1M characters"},
    ],
}
MULTI: dict[str, Any] = {
    "question": "Which checks should run?",
    "header": "Checks",
    "multiSelect": True,
    "options": [
        {"label": "lint", "description": "flake8"},
        {"label": "types", "description": "mypy"},
        {"label": "perf", "description": "timing"},
    ],
}
CONFIG = build_run_config("ask-test")


def ask(cid: str = "q1", *questions: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "AskUserQuestion", "id": cid,
                                              "args": {"questions": list(questions or (QUESTION,))}}])


def denied(cid: str = "q1") -> ToolMessage:
    return ToolMessage(content="denied", tool_call_id=cid, status="error",
                       additional_kwargs={"is_error": True, "permission_behavior": "deny"})


# ── the tool ─────────────────────────────────────────────────────────────────

def test_the_tool_is_upstreams():
    tool = build_ask_user_question_tool()
    assert tool.name == "AskUserQuestion"
    assert tool.prompt == (
        "Use this tool when you need to ask the user questions during execution. This allows you to:\n"
        "1. Gather user preferences or requirements\n"
        "2. Clarify ambiguous instructions\n"
        "3. Get decisions on implementation choices as you work\n"
        "4. Offer choices to the user about what direction to take.\n\n"
        "Usage notes:\n"
        "- Users will always be able to select \"Other\" to provide custom text input\n"
        "- Use multiSelect: true to allow multiple answers to be selected for a question\n"
        "- If you recommend a specific option, make that the first option in the list and add "
        "\"(Recommended)\" at the end of the label\n\n"
        "Plan mode note: In plan mode, use this tool to clarify requirements or choose between approaches BEFORE "
        "finalizing your plan. Do NOT use this tool to ask \"Is my plan ready?\" or \"Should I proceed?\" - use "
        "ExitPlanMode for plan approval. IMPORTANT: Do not reference \"the plan\" in your questions (e.g., \"Do you "
        "have feedback about the plan?\", \"Does the plan look good?\") because the user cannot see the plan in the "
        "UI until you call ExitPlanMode. If you need plan approval, use ExitPlanMode instead.\n\n"
        "TDD plan note: Do NOT use this tool to ask \"Should I proceed?\" or whether to start the next "
        "sub-requirement — the plan advances on its own once a cycle is complete. Use it only for decisions "
        "the plan, the specification and the code cannot answer."
    )
    assert tool.is_read_only({}) is True and tool.is_concurrency_safe({}) is True
    assert tool.requires_user_interaction() is True
    assert tool.max_result_size_chars == 100_000
    assert tool.description({"questions": [QUESTION, MULTI]}) == "Ask the user 2 question(s)"
    assert tool.input_schema is ASK_USER_QUESTION_INPUT_SCHEMA


def test_the_schema_bounds_are_upstreams():
    schema = ASK_USER_QUESTION_INPUT_SCHEMA
    questions = schema["properties"]["questions"]
    assert (questions["minItems"], questions["maxItems"]) == (1, 4)
    options = questions["items"]["properties"]["options"]
    assert (options["minItems"], options["maxItems"]) == (2, 4)
    assert questions["items"]["required"] == ["question", "header", "options"]
    assert options["items"]["required"] == ["label", "description"]
    assert schema["required"] == ["questions"] and schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"questions", "answers", "annotations", "metadata"}
    assert "max 12 chars" in questions["items"]["properties"]["header"]["description"]


@pytest.mark.parametrize("questions, unique", [
    ([QUESTION, MULTI], True),
    ([QUESTION, QUESTION], False),
    ([{**QUESTION, "options": [{"label": "a"}, {"label": "a"}]}], False),
    ([{**QUESTION, "options": [{"label": "a"}, {"label": "b"}]}, {**MULTI, "options": [{"label": "a"}]}], True),
])
def test_uniqueness(questions: list[Any], unique: bool):
    assert questions_are_unique(questions) is unique
    tool = build_ask_user_question_tool()
    result = tool.validate_input({"questions": questions}, tool_context_for(AppStateStore(AppState())))
    assert result.valid is unique
    if not unique:
        assert (result.message, result.error_code) == (UNIQUENESS_MESSAGE, 1)


def test_the_permission_check_always_asks():
    tool = build_ask_user_question_tool()
    args = {"questions": [QUESTION]}
    result = asyncio.run(_awaited(tool.check_permissions(args, tool_context_for(AppStateStore(AppState())))))
    assert (result.behavior, result.message, result.updated_input) == ("ask", "Answer questions?", args)


async def _awaited(value: Any) -> Any:
    return await value if asyncio.iscoroutine(value) else value


def test_the_result_tells_the_model_the_answers():
    assert answers_text({"Q1?": "A", "Q2?": "B, C"}, None) == (
        'User has answered your questions: "Q1?"="A", "Q2?"="B, C". '
        "You can now continue with the user's answers in mind.")
    assert answers_text({"Q?": "A"}, {"Q?": {"preview": "<p>", "notes": "fine"}}) == (
        'User has answered your questions: "Q?"="A" selected preview:\n<p> user notes: fine. '
        "You can now continue with the user's answers in mind.")
    tool = build_ask_user_question_tool()
    result = asyncio.run(tool.call({"questions": [QUESTION], "answers": {"Q?": "A"}},
                                   tool_context_for(AppStateStore(AppState()))))
    assert result.content == answers_text({"Q?": "A"}, None)


def test_the_classifier_sees_the_question_texts():
    tool = build_ask_user_question_tool()
    assert to_auto_classifier_input(tool, {"questions": [QUESTION, MULTI]}) == (
        "Which size should the performance test use? | Which checks should run?")


def test_render_questions_for_a_report():
    assert render_questions([QUESTION]) == (
        "[Perf size] Which size should the performance test use?\n"
        "  1. Moderate (Recommended) — 400k characters, 5 s budget\n"
        "  2. Full — 1M characters")


# ── through the gate ────────────────────────────────────────────────────────

def run_call(ctx: Any, args: dict[str, Any] | None = None) -> Any:
    tool = build_ask_user_question_tool()
    ctx.tools = (tool,)
    call = ToolCall(name=tool.name, args=args or {"questions": [QUESTION]}, id="q1")
    return asyncio.run(run_tool_use(call, AIMessage(content=""), ctx, (tool,)))


@pytest.mark.parametrize("mode", [PermissionMode.ACCEPT_EDITS, PermissionMode.BYPASS_PERMISSIONS])
def test_with_nobody_to_ask_it_is_denied_in_every_mode(mode: PermissionMode):
    perm = ToolPermissionContext(mode=mode, should_avoid_permission_prompts=True,
                                 is_bypass_permissions_mode_available=True)
    ctx = tool_context_for(AppStateStore(AppState(tool_permission_context=perm)), workspace=FakeWorkspace())
    message = run_call(ctx).message
    assert (message.status, message.content) == ("error", auto_reject_message("AskUserQuestion"))
    assert message.additional_kwargs["permission_behavior"] == "deny"


def interactive(answers: dict[str, str] | None, asker: Any = None) -> tuple[Any, list[Any]]:
    seen: list[Any] = []

    async def ask_questions(questions: tuple[dict[str, Any], ...]) -> dict[str, str] | None:
        seen.append(questions)
        return answers

    async def no_permission_prompt(prompt: PermissionPrompt) -> PermissionAnswer:
        raise AssertionError("AskUserQuestion has its own question")

    perm = ToolPermissionContext(mode=PermissionMode.DEFAULT, should_avoid_permission_prompts=False)
    ctx = tool_context_for(AppStateStore(AppState(tool_permission_context=perm)), workspace=FakeWorkspace())
    ctx.can_use_tool = build_interactive_can_use_tool(asker or no_permission_prompt, ask_questions)
    return ctx, seen


def test_the_user_answers_and_the_model_hears_it():
    ctx, seen = interactive({QUESTION["question"]: "Moderate (Recommended)"})
    message = run_call(ctx).message
    assert seen == [(QUESTION,)]
    assert message.status == "success"
    assert message.content == answers_text({QUESTION["question"]: "Moderate (Recommended)"}, None)
    assert ctx.cancel.cancelled is False


def test_declining_rejects_and_ends_the_turn():
    ctx, _ = interactive(None)
    message = run_call(ctx).message
    assert (message.status, message.content) == ("error", REJECT_MESSAGE)
    assert message.additional_kwargs["permission_behavior"] == "ask"
    assert ctx.cancel.cancelled is True


def test_without_a_question_asker_it_falls_back_to_the_permission_prompt():
    prompts: list[PermissionPrompt] = []

    async def asker(prompt: PermissionPrompt) -> PermissionAnswer:
        prompts.append(prompt)
        return PermissionAnswer("yes")

    perm = ToolPermissionContext(mode=PermissionMode.DEFAULT, should_avoid_permission_prompts=False)
    ctx = tool_context_for(AppStateStore(AppState(tool_permission_context=perm)), workspace=FakeWorkspace())
    ctx.can_use_tool = build_interactive_can_use_tool(asker)
    assert run_call(ctx).message.status == "success"
    assert [p.tool_name for p in prompts] == ["AskUserQuestion"]


# ── awaiting the user ───────────────────────────────────────────────────────

def test_a_denied_question_is_awaiting_an_answer():
    messages = [HumanMessage(content="go"), ask("q1"), denied("q1"), AIMessage(content="I need your decision.")]
    assert awaiting_user_answer(messages) == (QUESTION,)


def test_a_meta_message_after_it_does_not_answer_it():
    messages = [ask("q1"), denied("q1"), HumanMessage(content="reminder", additional_kwargs={"is_meta": True})]
    assert awaiting_user_answer(messages) == (QUESTION,)


@pytest.mark.parametrize("messages", [
    [],
    [HumanMessage(content="go")],
    # answered
    [ask("q1"), ToolMessage(content="User has answered", tool_call_id="q1")],
    # refused for its input: not a question asked
    [ask("q1"), ToolMessage(content="bad", tool_call_id="q1", status="error", additional_kwargs={"is_error": True})],
    # the user's rejection is an answer
    [ask("q1"), ToolMessage(content="no", tool_call_id="q1", status="error",
                            additional_kwargs={"permission_behavior": "ask"})],
    # another tool denied
    [AIMessage(content="", tool_calls=[{"name": "Bash", "id": "b1", "args": {}}]), denied("b1")],
    # the user spoke since
    [ask("q1"), denied("q1"), HumanMessage(content="use the moderate size")],
])
def test_nothing_is_awaiting(messages: list[Any]):
    assert awaiting_user_answer(messages) is None


def test_the_latest_unanswered_question_is_reported():
    messages = [ask("q1"), denied("q1"), ask("q2", MULTI), denied("q2")]
    assert awaiting_user_answer(messages) == (MULTI,)


def stop_state(messages: list[Any], *, agent_id: str | None = None, tools: tuple[Any, ...] = ()) -> Any:
    store = AppStateStore(AppState(phase_ledger=PhaseLedger()))
    ctx = tool_context_for(store, agent_id=agent_id)
    ctx.tools = tools
    return initial_loop_state(tuple(messages), ctx)


def test_the_stop_hooks_let_the_turn_end_and_wait():
    state = stop_state([HumanMessage(content="go"), ask("q1"), denied("q1"), AIMessage(content="Which one?")])
    assert asyncio.run(_awaited(build_stop_hooks_runner()(state, CONFIG))) == StopHookResult(prevent_continuation=True)


def test_a_worker_is_not_waited_for():
    state = stop_state([ask("q1"), denied("q1")], agent_id="agent_1")
    assert asyncio.run(_awaited(build_stop_hooks_runner()(state, CONFIG))) == StopHookResult()


def test_the_tdd_refusal_names_the_tool_only_when_the_pool_has_it():
    plain = asyncio.run(tdd_phase_incomplete_hook(stop_state([HumanMessage(content="go")]), CONFIG))
    assert ASK_THE_USER not in str(plain.blocking_errors[0].content)
    state = stop_state([HumanMessage(content="go")], tools=(build_ask_user_question_tool(),))
    hinted = asyncio.run(tdd_phase_incomplete_hook(state, CONFIG))
    assert str(hinted.blocking_errors[0].content) == f"{plain.blocking_errors[0].content} {ASK_THE_USER}"
    # Upstream's own escalation rule (`constants/prompts.ts`, Doing tasks), not an invitation to ask at every
    # refusal: a live run asked the same question three times with the old hint (2026-10-28).
    assert ASK_THE_USER == ("Escalate to the user with AskUserQuestion only when you're genuinely stuck after "
                            "investigation, not as a first response to friction.")


# ── the REPL ────────────────────────────────────────────────────────────────

def test_render_question():
    assert render_question(QUESTION, 1, 1) == (
        "? [Perf size] Which size should the performance test use?\n"
        "  1. Moderate (Recommended) — 400k characters, 5 s budget\n"
        "  2. Full — 1M characters\n"
        "  3. Other — type your own answer\n"
        "Choose 1-2, or type your own answer.")
    assert render_question(MULTI, 2, 3).splitlines()[0] == "? [Checks] Which checks should run? (2/3)"
    assert render_question(MULTI, 1, 1).splitlines()[-1] == (
        "Choose one or more of 1-3 (e.g. \"1,3\"), or type your own answer.")


@pytest.mark.parametrize("question, line, answer", [
    (QUESTION, "1", "Moderate (Recommended)"),
    (QUESTION, " 2 ", "Full"),
    (QUESTION, "3 half a million", "half a million"),
    (QUESTION, "3", None),
    (QUESTION, "4", None),
    (QUESTION, "0", None),
    (QUESTION, "", None),
    (QUESTION, "1,2", "1,2"),  # not multiSelect: the text is the answer
    (QUESTION, "use 200k", "use 200k"),
    (MULTI, "1,3", "lint, perf"),
    (MULTI, "3 1 3", "perf, lint"),
    (MULTI, "1,5", None),
    (MULTI, "4 just lint", "just lint"),
    (MULTI, "lint only", "lint only"),
])
def test_parse_question_answer(question: dict[str, Any], line: str, answer: str | None):
    assert parse_question_answer(question, line) == answer


class Model:
    def __init__(self, turns: list[list[Any]]) -> None:
        self.turns = list(turns)
        self.seen: list[Any] = []

    async def __call__(self, state: Any, config: Any) -> AsyncIterator[Any]:
        self.seen.append(state)
        if not self.turns:
            raise AssertionError("model asked for more turns than scripted")
        for message in self.turns.pop(0):
            yield message


def deps_for(model: Model) -> Any:
    from app.query.deps import get_production_deps

    return dataclasses.replace(get_production_deps(), call_model=model)


FINISHED = TddPlan.from_texts(["a"])
FINISHED = dataclasses.replace(FINISHED, items=(dataclasses.replace(FINISHED.items[0], status=ItemStatus.DONE),))


async def repl_session(workspace: Any, tmp_path: Path, model: Model, script: Any) -> tuple[Repl, FakeIO]:
    io = FakeIO()
    holder: dict[str, Repl] = {}

    async def ask_permission(prompt: PermissionPrompt) -> PermissionAnswer:
        return await holder["repl"].ask_permission(prompt)

    async def ask_questions(questions: tuple[dict[str, Any], ...]) -> dict[str, str] | None:
        return await holder["repl"].ask_questions(questions)

    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=False)
    async with open_session_run(run_id=f"loop-ask-{id(model)}", plan=FINISHED, workspace=workspace,
                                deps=deps_for(model), permission_context=perm, project_root=tmp_path,
                                home=tmp_path / "home", managed_dir=tmp_path / "etc",
                                can_use_tool=build_interactive_can_use_tool(ask_permission, ask_questions)) as run:
        repl = Repl(run, io)
        holder["repl"] = repl
        runner = asyncio.create_task(repl.run_session("start"))
        await script(repl, io)
        await asyncio.wait_for(runner, 30)
    return repl, io


def test_the_repl_asks_each_question_and_the_model_continues(python_workspace, tmp_path):
    model = Model([[ask("q1", QUESTION, MULTI)], [AIMessage(content="thanks")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: "? [Perf size]" in io.text())
        io.lines.put_nowait("")  # asks again
        await until(lambda: "Choose 1-3, or type your own answer." in io.out)
        io.lines.put_nowait("1")
        await until(lambda: "? [Checks] Which checks should run? (2/2)" in io.text())
        io.lines.put_nowait("1,2")
        await until(lambda: not repl.busy)
        io.lines.put_nowait("/exit")

    asyncio.run(repl_session(python_workspace, tmp_path, model, script))
    (result,) = [m for m in model.seen[1].messages if isinstance(m, ToolMessage)]
    assert result.content == answers_text(
        {QUESTION["question"]: "Moderate (Recommended)", MULTI["question"]: "lint, types"}, None)


def test_esc_during_a_question_declines_and_ends_the_turn(python_workspace, tmp_path):
    model = Model([[ask("q1")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: repl.pending is not None)
        repl.interrupt()
        await until(lambda: not repl.busy)
        io.lines.put_nowait("/exit")

    repl, io = asyncio.run(repl_session(python_workspace, tmp_path, model, script))
    assert repl.last_terminal == Terminal.ABORTED_TOOLS
    assert "Interrupted." in io.text()


# ── headless: stop, then resume with the answer ─────────────────────────────

def headless_options(tmp_path: Path, model: Model) -> dict[str, Any]:
    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=True)
    return {"deps": deps_for(model), "permission_context": perm, "project_root": tmp_path,
            "home": tmp_path / "home", "managed_dir": tmp_path / "etc"}


def test_headless_stops_on_the_question_and_resumes_with_the_answer(python_workspace, tmp_path):
    save_plan(plan_path(session_run_id("ask1"), str(python_workspace.root)), TddPlan.from_texts(["perf test"]))
    asking = Model([[ask("q1")], [AIMessage(content="I need your decision on the size.")]])
    outcome = asyncio.run(run_headless("ask1", None, workspace=python_workspace,
                                       **headless_options(tmp_path, asking)))
    assert (outcome.terminal, outcome.question) == (Terminal.STOP_HOOK_PREVENTED, (QUESTION,))
    assert outcome.planned and outcome.completed is False and outcome.plan.done_count == 0
    (result,) = [m for m in asking.seen[1].messages if isinstance(m, ToolMessage)]
    assert result.content == auto_reject_message("AskUserQuestion")

    resumed = Model([[AIMessage(content="stop")]])
    resumed_outcome = asyncio.run(run_headless("ask1", None, workspace=python_workspace,
                                               prompt="Moderate (Recommended)",
                                               **headless_options(tmp_path, _with_esc(resumed))))
    human = [m for m in resumed.seen[0].messages
             if isinstance(m, HumanMessage) and not m.additional_kwargs.get("is_meta")]
    assert human[-1].content == "Moderate (Recommended)"
    assert resumed_outcome.question is None


def _with_esc(model: Model) -> Model:
    """After its script, the user presses Esc: the stop hook would otherwise send it back forever."""
    original = model.__call__

    class Patient(Model):
        async def __call__(self, state: Any, config: Any) -> AsyncIterator[Any]:
            if not model.turns:
                state.tool_context.cancel.cancel()
                model.seen.append(state)
                yield AIMessage(content="stop")
                return
            async for message in original(state, config):
                yield message

    patient = Patient([])
    patient.seen = model.seen
    return patient


def test_waiting_text():
    assert waiting_text("tdd-1", [QUESTION], headless=True) == (
        "\n❓ The agents are waiting for your answer:\n" + render_questions([QUESTION])
        + "\n\n   Answer with: python -m app.main -p --thread-id tdd-1 \"<your answer>\"")
    assert waiting_text("tdd-1", [QUESTION], headless=False).endswith(
        "python -m app.main --thread-id tdd-1 \"<your answer>\"")
