"""`processUserInput` and `QueryEngine.submit_message` / `permissionDenials` / `interrupt` (Part 8)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.query_engine import QueryEngine, ResultMessage
from app.services.tools.tool_execution import run_tool_use
from app.skills.load_skills_dir import SkillRegistry
from app.state.app_state_store import AppStateStore
from app.tool import CancelToken, ToolResult, build_tool, tool_context_for
from app.types.hooks import AggregatedHookResult
from app.types.permissions import PermissionResult, ToolPermissionContext
from app.utils.messages import HookBlockingError
from app.utils.process_user_input.process_user_input import (
    LOCAL_COMMAND_CAVEAT,
    model_only_skill_message,
    process_user_input,
)
from tests.hook_doubles import ScriptedDispatcher
from tests.test_commands_registry import _skill
from tests.test_loop_engine import CONFIG, FakeModel, make_fake_deps
from tests.test_loop_query_engine import FakeTranscript

ATTACHMENT = HumanMessage(content="<attachment>", additional_kwargs={"is_meta": True})


@pytest.fixture(autouse=True)
def fixed_attachments(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[Any], str | None]]:
    """The user-input attachments as one marker, and what each call was handed."""
    calls: list[tuple[list[Any], str | None]] = []

    async def fake(context: Any, messages: Any, *, user_input: str | None = None) -> list[Any]:
        calls.append((list(messages), user_input))
        return [ATTACHMENT]

    monkeypatch.setattr("app.utils.attachments.get_attachment_messages", fake)
    return calls


def _ctx(dispatcher: Any = None, skills: tuple[Any, ...] = (), store: AppStateStore | None = None) -> Any:
    return tool_context_for(store or AppStateStore(), hook_dispatcher=dispatcher or ScriptedDispatcher(),
                            skill_registry=SkillRegistry(list(skills)),
                            permission_context=ToolPermissionContext(should_avoid_permission_prompts=True))


def _process(text: str, ctx: Any, history: tuple[Any, ...] = (), **kw: Any) -> Any:
    return asyncio.run(process_user_input(text, ctx, list(history), permission_mode="default", **kw))


def _contents(messages: list[Any]) -> list[Any]:
    return [m.content for m in messages]


# ── processUserInput ─────────────────────────────────────────────────────────

def test_a_text_prompt_is_the_message_then_its_attachments_then_hook_context(fixed_attachments):
    d = ScriptedDispatcher(script={"UserPromptSubmit": [AggregatedHookResult(additional_contexts=("ctx",))]})
    earlier = HumanMessage(content="earlier")
    result = _process("build it", _ctx(d), (earlier,))
    assert result.should_query and result.stopped_reason is None and result.result_text is None
    contents = _contents(result.messages)
    assert contents[:2] == ["build it", "<attachment>"]
    assert "UserPromptSubmit hook additional context: ctx" in str(contents[2]) and len(contents) == 3
    assert fixed_attachments == [([earlier, HumanMessage(content="build it")], "build it")]
    assert d.seen[0][1]["prompt"] == "build it" and d.seen[0][1]["permission_mode"] == "default"


def test_user_messages_stand_in_for_the_plain_message():
    resumed = HumanMessage(content="as it was", id="u1")
    result = _process("as it was", _ctx(), user_messages=[resumed])
    assert result.messages[0] is resumed


def test_a_blocking_hook_drops_everything_said_and_names_why():
    d = ScriptedDispatcher(script={"UserPromptSubmit": [
        AggregatedHookResult(blocking_error=HookBlockingError(blocking_error="nope", command="c"))]})
    result = _process("build it", _ctx(d))
    assert (result.messages, result.should_query) == ([], False)
    assert result.stopped_reason == "UserPromptSubmit operation blocked by hook:\nnope\n\nOriginal prompt: build it"


def test_a_stopping_hook_keeps_the_prompt_and_what_hooks_said_before_it():
    d = ScriptedDispatcher(script={"UserPromptSubmit": [
        AggregatedHookResult(additional_contexts=("first",)),
        AggregatedHookResult(prevent_continuation=True, stop_reason="later")]})
    result = _process("build it", _ctx(d))
    contents = _contents(result.messages)
    assert contents[:2] == ["build it", "<attachment>"]
    assert "first" in str(contents[2])
    assert contents[3] == "Operation stopped by hook: later" and len(contents) == 4
    assert (result.should_query, result.stopped_reason) == (False, "Operation stopped by hook: later")


def test_an_unknown_command_is_answered_and_never_queried(fixed_attachments):
    d = ScriptedDispatcher()
    result = _process("/nope x", _ctx(d))
    assert _contents(result.messages) == [LOCAL_COMMAND_CAVEAT, "Unknown skill: nope\nArgs from unknown skill: x"]
    assert result.messages[0].additional_kwargs == {"is_meta": True}
    assert (result.should_query, result.result_text) == (False, "Unknown skill: nope\nArgs from unknown skill: x")
    assert d.seen == [] and fixed_attachments == []  # no hooks, no attachments


@pytest.mark.parametrize("text", ["/usr", "/a.b c", "/"])
def test_a_path_or_a_non_command_is_a_text_prompt(text):
    result = _process(text, _ctx())
    assert result.should_query and _contents(result.messages)[0] == text


def test_skip_slash_commands_sends_a_command_as_text():
    result = _process("/nope", _ctx(), skip_slash_commands=True)
    assert result.should_query and _contents(result.messages)[0] == "/nope"


def test_a_typed_skill_loads_it_with_its_tools_model_and_attachments(fixed_attachments):
    d = ScriptedDispatcher()
    skill = _skill(allowed_tools=("ReadFile",), model="haiku")
    result = _process("/design the calculator", _ctx(d, (skill,)), session_id="s9")
    metadata, content, attachment = result.messages
    assert str(metadata.content).endswith("<command-args>the calculator</command-args>")
    assert "Design tests for the calculator." in str(content.content) and attachment is ATTACHMENT
    assert (result.allowed_tools, result.model, result.should_query) == (("ReadFile",), "haiku", True)
    said, user_input = fixed_attachments[0]
    assert said == [metadata, content] and user_input == content.content
    assert d.seen[0][1]["prompt"] == "/design the calculator"


def test_a_model_only_skill_typed_by_the_user_is_refused():
    result = _process("/design", _ctx(skills=(_skill(user_invocable=False),)))
    assert _contents(result.messages) == ["/design", model_only_skill_message("design")]
    assert (result.should_query, result.result_text) == (False, model_only_skill_message("design"))
    assert model_only_skill_message("x") == ('This skill can only be invoked by TDDAgents, not directly by users. '
                                             'Ask TDDAgents to use the "x" skill for you.')


def test_without_a_skill_registry_every_command_is_unknown():
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=ScriptedDispatcher())
    assert _process("/design", ctx).result_text == "Unknown skill: design"


def test_coordinator_mode_describes_the_skill_for_workers():
    result = _process("/design x", _ctx(skills=(_skill(),)), coordinator=True)
    assert str(result.messages[1].content).startswith('Skill "/design" is available for workers.')


# ── QueryEngine.submit_message ───────────────────────────────────────────────

def _engine(model: FakeModel, ctx: Any, transcript: FakeTranscript | None = None) -> QueryEngine:
    return QueryEngine(CONFIG, make_fake_deps(model), transcript=transcript or FakeTranscript(), session_id="s1",
                       context=ctx, uuid=lambda: "u")


def _submit(engine: QueryEngine, prompt: str, **kw: Any) -> list[Any]:
    async def go() -> list[Any]:
        return [e async for e in engine.submit_message(prompt, **kw)]

    return asyncio.run(go())


def test_a_prompt_runs_the_loop_after_the_conversation_and_prefix():
    model = FakeModel([[AIMessage(content="done")]])
    ctx = _ctx()
    engine = _engine(model, ctx)
    engine.mutable_messages = [HumanMessage(content="before")]
    events = _submit(engine, "build it", prefix=[HumanMessage(content="hello")])
    sent = _contents(list(model.seen[0].messages))
    assert sent == ["before", "hello", "build it", "<attachment>"]
    assert model.seen[0].tool_context.messages == model.seen[0].messages
    assert isinstance(events[-1], ResultMessage) and events[-1].result == "done"
    assert engine.get_messages() is engine.mutable_messages and engine.get_messages()[-1].content == "done"
    assert engine.input_result is not None and engine.input_result.should_query


def test_a_turn_that_does_not_query_is_recorded_and_answered_by_its_result():
    transcript = FakeTranscript()
    model = FakeModel([])
    engine = _engine(model, _ctx(), transcript)
    engine.mutable_messages = [HumanMessage(content="before")]
    events = _submit(engine, "/nope")
    assert model.seen == []
    [result] = events
    assert isinstance(result, ResultMessage)
    assert (result.subtype, result.is_error, result.result, result.terminal_reason) == (
        "success", False, "Unknown skill: nope", "")
    assert (result.num_turns, result.stop_reason, result.total_cost_usd, result.uuid) == (2, None, 0.0, "u")
    assert result.usage == {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0}
    assert _contents(engine.get_messages()) == ["before", LOCAL_COMMAND_CAVEAT, "Unknown skill: nope"]
    assert transcript.calls == [engine.get_messages()]
    assert engine.terminal is None and engine.result is result


def test_a_typed_skill_applies_its_tools_and_model_to_the_turn():
    model = FakeModel([[AIMessage(content="done")]])
    ctx = _ctx(skills=(_skill(allowed_tools=("ReadFile",), model="opus"),))
    _submit(_engine(model, ctx), "/design it")
    turn = model.seen[0].tool_context
    assert turn.main_loop_model != ctx.main_loop_model and "opus" in str(turn.main_loop_model)
    assert "ReadFile" in str(turn.get_app_state().tool_permission_context.always_allow_rules)
    assert "ReadFile" not in str(ctx.get_app_state().tool_permission_context.always_allow_rules)


def test_a_cancelled_token_is_renewed_for_the_next_turn():
    model = FakeModel([[AIMessage(content="done")]])
    ctx = _ctx()
    engine = _engine(model, ctx)
    old = ctx.cancel
    engine.interrupt()
    assert old.cancelled
    _submit(engine, "again")
    assert ctx.cancel is not old and not ctx.cancel.cancelled
    assert model.seen[0].tool_context.cancel is ctx.cancel


def test_a_live_token_is_kept():
    ctx = _ctx()
    token = ctx.cancel
    _submit(_engine(FakeModel([[AIMessage(content="d")]]), ctx), "x")
    assert ctx.cancel is token


# ── permissionDenials ────────────────────────────────────────────────────────

async def _ran(args: dict[str, Any], ctx: Any) -> ToolResult:
    return ToolResult(content="ran")


def _tool(behavior: str = "deny") -> Any:
    """A tool whose own check answers `behavior`."""
    return build_tool(name="Probe", prompt="p", call=_ran, input_schema={"type": "object"},
                      is_concurrency_safe=lambda a: True,
                      check_permissions=lambda a, c: PermissionResult(behavior=behavior, message="no"))


def _run_tool(tool: Any, ctx: Any) -> Any:
    return asyncio.run(run_tool_use({"name": tool.name, "args": {"n": 1}, "id": "c7"}, AIMessage(content=""),
                                    ctx, [tool]))


def test_every_call_not_allowed_is_kept_for_the_result():
    ctx = _ctx()
    engine = _engine(FakeModel([[AIMessage(content="done")]]), ctx)
    assert ctx.record_permission_denial == engine._record_denial
    assert _run_tool(_tool(), ctx).message.status == "error"
    assert engine.permission_denials == [{"tool_name": "Probe", "tool_use_id": "c7", "tool_input": {"n": 1}}]
    events = _submit(engine, "x")
    assert events[-1].permission_denials == ({"tool_name": "Probe", "tool_use_id": "c7", "tool_input": {"n": 1}},)


def test_an_allowed_call_is_not_a_denial():
    ctx = _ctx()
    seen: list[Any] = []
    ctx.record_permission_denial = lambda *a: seen.append(a)
    assert str(_run_tool(_tool("allow"), ctx).message.content) == "ran"
    assert seen == []
    assert _run_tool(_tool("ask"), ctx).message.status == "error"  # nobody to ask: denied
    assert seen == [("Probe", "c7", {"n": 1})]


def test_a_denial_without_an_engine_records_nothing():
    ctx = _ctx()
    assert ctx.record_permission_denial is None
    assert _run_tool(_tool(), ctx).message.status == "error"


def test_an_engine_without_a_context_keeps_no_recorder():
    engine = QueryEngine(CONFIG, make_fake_deps(FakeModel([])), transcript=FakeTranscript(), session_id="s")
    assert engine.context is None and engine.permission_denials == [] and engine.input_result is None


def test_cancel_token_is_shared_by_interrupt():
    ctx = _ctx()
    ctx.cancel = CancelToken()
    _engine(FakeModel([]), ctx).interrupt()
    assert ctx.cancel.cancelled


# ── print.ts's turn and the REPL ─────────────────────────────────────────────

def _run(engine: QueryEngine) -> Any:
    from types import SimpleNamespace

    shown: list[Any] = []
    return SimpleNamespace(engine=engine, ctx=engine.context, store=None, run_id="r", stopped_reason=None,
                           on_message=shown.append, shown=shown, config=engine.config, deps=engine.deps,
                           transcript=engine.transcript, messages=engine.mutable_messages,
                           user_specified_model=None, max_turns=None, max_budget_usd=None,
                           json_schema=None)


def test_a_turn_a_hook_stopped_ends_hook_stopped_with_the_prompt_kept():
    from app.cli.print import _submit
    from app.query.transitions import Terminal

    d = ScriptedDispatcher(script={"UserPromptSubmit": [
        AggregatedHookResult(prevent_continuation=True, stop_reason="wait")]})
    run = _run(_engine(FakeModel([]), _ctx(d)))
    assert asyncio.run(_submit(run, [HumanMessage(content="old")], "build it")) == Terminal.HOOK_STOPPED
    assert run.stopped_reason == "Operation stopped by hook: wait"
    assert _contents(run.engine.get_messages()) == [
        "old", "build it", "<attachment>", "Operation stopped by hook: wait"]
    assert isinstance(run.shown[-1], ResultMessage)


def test_an_unknown_command_ends_the_turn_without_a_terminal():
    from app.cli.print import _submit
    from app.screens.repl import render_message

    run = _run(_engine(FakeModel([]), _ctx()))
    assert asyncio.run(_submit(run, [], "/nope")) is None
    assert run.stopped_reason is None
    assert render_message(run.shown[-1]) == "Unknown skill: nope"


def test_the_repl_shows_only_a_result_that_never_queried():
    from dataclasses import replace

    from app.screens.repl import render_message

    result = ResultMessage(subtype="success", is_error=False, duration_ms=0, duration_api_ms=0, num_turns=0,
                           stop_reason=None, session_id="s", total_cost_usd=0.0, usage={}, model_usage={},
                           terminal_reason="", uuid="u", result="said")
    assert render_message(result) == "said"
    assert render_message(replace(result, result="")) is None
    assert render_message(replace(result, terminal_reason="completed")) is None
