"""
Mutation-driven pins for the prompt and agent hook executors (`exec_prompt.py`,
`exec_agent.py`): result identity, attachment fields on every error path, the exact verifier
prompt, cancellation of abandoned work, and the production builders driven by a fake model
factory (never a live provider).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.loop.context import (
    AppState,
    AppStateStore,
    CancelToken,
    discard_app_state_update,
    tool_context_for,
)
from app.loop.hooks import exec_agent as ea
from app.loop.hooks import exec_prompt as ep
from app.loop.hooks.exec_agent import (
    STRUCTURED_OUTPUT_PROMPT,
    AgentHookRequest,
    AgentHookRunOutcome,
    StructuredOutputHolder,
    build_agent_hook_runner,
    create_structured_output_tool,
    exec_agent_hook,
)
from app.loop.hooks.exec_prompt import HookCancelled, exec_prompt_hook, run_with_signal
from app.loop.hooks.schemas import AgentHook, PromptHook
from app.loop.ledger import PhaseLedger
from app.loop.streaming.abort import AbortController

HOOK_NAME = "PreToolUse:Bash"
HOOK_EVENT = "PreToolUse"
TOOL_USE_ID = "tu-9"


def _await(awaitable: Any) -> Any:
    """`asyncio.run` for an awaitable that is not typed as a coroutine."""
    async def wrapper() -> Any:
        return await awaitable

    return asyncio.run(wrapper())


def _aborted() -> CancelToken:
    token = CancelToken()
    token.cancel()
    return token


# ── prompt hook ──────────────────────────────────────────────────────────────


class Caller:
    def __init__(self, reply: Any = '{"ok": true}', error: Exception | None = None) -> None:
        self.reply, self.error, self.calls = reply, error, 0

    async def __call__(self, messages, system_prompt, model, output_schema):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.reply


def _prompt(hook: PromptHook, caller: Caller, signal: Any = None) -> Any:
    return asyncio.run(
        exec_prompt_hook(
            hook, HOOK_NAME, HOOK_EVENT, "{}", signal, None, None, TOOL_USE_ID, model_caller=caller, default_model="m"
        )
    )


def _assert_non_blocking(result: Any, hook: Any, stderr: str) -> None:
    assert result.hook is hook
    assert result.outcome == "non_blocking_error"
    att = result.attachment
    assert (att.type, att.hook_name, att.tool_use_id, att.hook_event) == (
        "hook_non_blocking_error", HOOK_NAME, TOOL_USE_ID, HOOK_EVENT)
    assert att.stderr == stderr
    assert att.exit_code == 1


def test_prompt_result_carries_its_hook_on_every_path():
    hook = PromptHook(prompt="p", timeout=0.05)
    assert _prompt(hook, Caller(), _aborted()).hook is hook
    assert _prompt(hook, Caller('{"ok": false, "reason": "r"}')).hook is hook
    _assert_non_blocking(_prompt(hook, Caller("nope")), hook, "JSON validation failed")


def test_prompt_schema_failure_names_the_hook():
    hook = PromptHook(prompt="p")
    result = _prompt(hook, Caller('{"ok": "yes"}'))
    _assert_non_blocking(result, hook, "Schema validation failed: ok: Expected boolean")
    assert result.attachment.stdout == '{"ok": "yes"}'


def test_prompt_caller_error_names_the_hook():
    hook = PromptHook(prompt="p")
    result = _prompt(hook, Caller(error=RuntimeError("down")))
    _assert_non_blocking(result, hook, "Error executing prompt hook: down")
    assert result.attachment.stdout == ""


def test_a_nonzero_number_reply_is_a_schema_failure_not_a_parse_failure():
    hook = PromptHook(prompt="p")
    result = _prompt(hook, Caller("5"))
    _assert_non_blocking(result, hook, "Schema validation failed: Expected object, received int")


def test_run_with_signal_never_starts_work_when_already_aborted():
    started: list[bool] = []

    async def op() -> str:
        started.append(True)
        return "x"

    async def go() -> None:
        with pytest.raises(HookCancelled):
            await run_with_signal(op, _aborted(), 1000)
        await asyncio.sleep(0)

    asyncio.run(go())
    assert started == []


def test_run_with_signal_cancels_the_abandoned_operation():
    cancelled: list[bool] = []

    async def op() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def go() -> list[bool]:
        with pytest.raises(HookCancelled):
            await run_with_signal(op, None, 30)
        await asyncio.sleep(0.01)
        # Checked inside the loop: `asyncio.run` cancels leftovers at shutdown anyway.
        return list(cancelled)

    assert asyncio.run(go()) == [True]


def test_text_of_ignores_non_text_blocks_and_defaults_missing_text():
    assert ep._text_of([5, {"type": "text"}, {"type": "image", "text": "no"}, "a"]) == "a"
    assert ep._text_of(None) == ""


@dataclass
class _Reply:
    content: Any


def _fake_factory(monkeypatch, reply: Any) -> list[Any]:
    import app.utils.chat_model_factory as factory

    seen: list[Any] = []

    class Chat:
        async def ainvoke(self, messages):
            seen.append(("invoke", messages))
            return reply

    def get_chat_model(provider, **kwargs):
        seen.append(("model", provider, kwargs))
        return Chat()

    monkeypatch.setattr(factory, "get_chat_model", get_chat_model)
    return seen


def test_production_prompt_caller_uses_the_provider_model_and_system_prompt(monkeypatch):
    from app.config.config import Config

    seen = _fake_factory(monkeypatch, _Reply([{"type": "text", "text": '{"ok": true}'}]))
    caller = ep.build_prompt_model_caller()
    question = HumanMessage(content="q")
    text = _await(caller([question], "SYS", "model-x", {}))
    assert text == '{"ok": true}'
    assert seen[0] == ("model", Config.CHAT_MODEL, {"model": "model-x"})
    kind, messages = seen[1]
    assert kind == "invoke"
    assert messages[0].content == "SYS" and messages[0].type == "system"
    assert messages[1:] == [question]


def test_production_prompt_caller_honours_an_explicit_provider(monkeypatch):
    seen = _fake_factory(monkeypatch, _Reply("plain"))
    assert _await(ep.build_prompt_model_caller("anthropic")([], "S", "m", {})) == "plain"
    assert seen[0] == ("model", "anthropic", {"model": "m"})


def test_production_prompt_caller_reads_a_reply_without_content_as_empty(monkeypatch):
    _fake_factory(monkeypatch, SimpleNamespace())
    assert _await(ep.build_prompt_model_caller()([], "S", "m", {})) == ""


# ── agent hook: exec_agent_hook ──────────────────────────────────────────────


class Runner:
    def __init__(self, outcome: AgentHookRunOutcome | None = None, delay: float = 0.0,
                 error: Exception | None = None) -> None:
        self.outcome = outcome or AgentHookRunOutcome(structured_output={"ok": True})
        self.delay, self.error = delay, error
        self.requests: list[AgentHookRequest] = []
        self.cancelled = False
        self.cancelled_in_loop = False

    async def __call__(self, request: AgentHookRequest) -> AgentHookRunOutcome:
        self.requests.append(request)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error is not None:
            raise self.error
        return self.outcome


def _agent(hook: AgentHook, runner: Runner, signal: Any = None, settle: float = 0.0) -> Any:
    async def go() -> Any:
        result = await exec_agent_hook(
            hook, HOOK_NAME, HOOK_EVENT, "{}", signal, "ctx", TOOL_USE_ID, None, "agentX",
            agent_runner=runner, default_model="m",
        )
        if settle:
            await asyncio.sleep(settle)
        runner.cancelled_in_loop = runner.cancelled
        return result

    return asyncio.run(go())


def test_verifier_system_prompt_is_upstream_verbatim():
    assert ea.agent_hook_system_prompt("/t/x.jsonl") == (
        "You are verifying a stop condition in TDDAgents. Your task is to verify that the agent "
        "completed the given plan. The conversation transcript is available at: /t/x.jsonl\n"
        "You can read this file to analyze the conversation history if needed.\n\n"
        "Use the available tools to inspect the codebase and verify the condition.\n"
        "Use as few steps as possible - be efficient and direct.\n\n"
        "When done, return your result using the StructuredOutput tool with:\n"
        "- ok: true if the condition is met\n"
        "- ok: false with reason if the condition is not met"
    )


def test_agent_result_carries_its_hook_on_every_path():
    hook = AgentHook(prompt="p", timeout=0.05)
    assert _agent(hook, Runner(), _aborted()).hook is hook
    assert _agent(hook, Runner(delay=5)).hook is hook
    assert _agent(hook, Runner(AgentHookRunOutcome())).hook is hook
    assert _agent(hook, Runner(AgentHookRunOutcome(structured_output={"ok": False}))).hook is hook


def test_agent_cancelled_after_abort_inside_runner_carries_its_hook():
    hook = AgentHook(prompt="p")

    async def runner(request: AgentHookRequest) -> AgentHookRunOutcome:
        request.abort.abort("x")
        raise RuntimeError("aborted inside")

    result = asyncio.run(exec_agent_hook(
        hook, HOOK_NAME, HOOK_EVENT, "{}", None, "ctx", TOOL_USE_ID, None, None, agent_runner=runner,
    ))
    assert (result.outcome, result.hook) == ("cancelled", hook)


def test_agent_pre_aborted_signal_never_starts_the_runner():
    runner = Runner()
    _agent(AgentHook(prompt="p"), runner, _aborted(), settle=0.01)
    assert runner.requests == []


def test_agent_timeout_aborts_with_its_reason_and_cancels_the_runner():
    runner = Runner(delay=5)
    result = _agent(AgentHook(prompt="p", timeout=0.05), runner, settle=0.01)
    assert result.outcome == "cancelled"
    assert runner.requests[0].abort.signal.reason == "hook_timeout_or_parent_abort"
    assert runner.cancelled_in_loop is True


def test_agent_runner_error_names_the_hook():
    hook = AgentHook(prompt="p")
    result = _agent(hook, Runner(error=RuntimeError("boom")))
    assert result.hook is hook
    att = result.attachment
    assert (att.type, att.hook_name, att.tool_use_id, att.hook_event) == (
        "hook_non_blocking_error", HOOK_NAME, TOOL_USE_ID, HOOK_EVENT)
    assert (att.stderr, att.stdout, att.exit_code) == ("Error executing agent hook: boom", "", 1)


# ── agent hook: structured output tool ───────────────────────────────────────


def test_structured_output_tool_prompt_and_description():
    tool = create_structured_output_tool(StructuredOutputHolder())
    assert tool.prompt == STRUCTURED_OUTPUT_PROMPT
    assert tool.description({}) == "Return structured output in the requested format"


# ── agent hook: production runner ────────────────────────────────────────────


def _parent():
    from app.loop.runner import build_builtin_tools

    store = AppStateStore(AppState(phase_ledger=PhaseLedger()))
    parent = tool_context_for(
        store, tools=tuple(build_builtin_tools()), permission_context=object(), refresh_tools=lambda: (),
    )
    return parent


def _capture_factory(turns: list[list[AIMessage]], seen: list[Any]):
    def factory(system_prompt: str):
        async def call(state, config):
            seen.append((state, config))
            for m in turns.pop(0) if turns else [AIMessage(content="thinking")]:
                yield m
        return call
    return factory


def _so(cid: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": cid, "name": "StructuredOutput", "args": {"ok": True}}])


def _request(parent: Any, **kwargs: Any) -> AgentHookRequest:
    fields: dict[str, Any] = {
        "prompt": "p", "system_prompt": "s", "model": "m", "tool_context": parent,
        "transcript_path": "/tr/x.jsonl", "abort": AbortController(),
    }
    fields.update(kwargs)
    return AgentHookRequest(**fields)


def _drive(runner: Any, request: AgentHookRequest) -> Any:
    async def go() -> Any:
        return await runner(request)

    return asyncio.run(go())


def test_runner_isolates_the_verifier_context():
    parent = _parent()
    seen: list[Any] = []
    request = _request(parent)
    out = _drive(build_agent_hook_runner(_capture_factory([[_so()]], seen)), request)
    assert out.structured_output == {"ok": True}
    state, config = seen[0]
    ctx = state.tool_context
    assert ctx.set_app_state is discard_app_state_update
    assert ctx.in_flight_tool_ids is not parent.in_flight_tool_ids
    assert ctx.permission_context is None
    assert ctx.refresh_tools is None
    rule = ctx.get_app_state().tool_permission_context.always_allow_rules[-1]
    assert rule.rule_content == "//tr/x.jsonl"
    assert config.run_id == ctx.agent_id and config.run_id.startswith("hook-agent-")
    assert config.gates.postgres_checkpointing is False
    # Aborting the request (here: by the structured output arriving) cancels the verifier.
    assert ctx.cancel.cancelled is True
    assert request.abort.signal.reason == "structured_output"


def test_runner_max_turns_aborts_with_its_reason():
    request = _request(_parent(), max_turns=2)
    out = _drive(build_agent_hook_runner(_capture_factory([], [])), request)
    assert out.hit_max_turns is True
    assert request.abort.signal.reason == "max_turns"


def test_runner_does_not_compact_on_prompt_too_long():
    """The verifier's compaction is a no-op: prompt-too-long ends its loop, it never retries."""
    seen: list[Any] = []
    ptl = AIMessage(content="", additional_kwargs={"api_error": "prompt_too_long"})
    out = _drive(build_agent_hook_runner(_capture_factory([[ptl], [_so()]], seen)), _request(_parent()))
    assert len(seen) == 1
    assert out.structured_output is None


def test_runner_closes_the_loop_before_returning(monkeypatch):
    from app.loop import engine

    real = engine.run_loop
    closed: list[bool] = []

    async def wrapped(*args: Any):
        try:
            async for event in real(*args):
                yield event
        finally:
            closed.append(True)

    monkeypatch.setattr(engine, "run_loop", wrapped)

    async def go() -> list[bool]:
        await build_agent_hook_runner(_capture_factory([[_so()]], []))(_request(_parent()))
        return list(closed)

    assert asyncio.run(go()) == [True]


# ── agent hook: the live model call, on a fake provider ──────────────────────


def test_live_call_model_binds_the_context_tools_and_streams_framed_blocks(monkeypatch):
    import app.loop.model as model_mod
    import app.loop.streaming.blocks as blocks_mod
    import app.utils.chat_model_factory as factory
    from app.config.config import Config

    seen: list[Any] = []

    class Chat:
        def bind_tools(self, tools):
            seen.append(("bind", tools))
            return self

        def astream(self, messages):
            seen.append(("stream", messages))
            return "STREAM"

    def get_chat_model(provider, **kwargs):
        seen.append(("model", provider, kwargs))
        return Chat()

    async def frame(stream):
        seen.append(("frame", stream))
        yield AIMessage(content="framed")

    monkeypatch.setattr(factory, "get_chat_model", get_chat_model)
    monkeypatch.setattr(model_mod, "convert_tool_to_langchain", lambda tool: ("lc", tool.name))
    monkeypatch.setattr(blocks_mod, "frame_content_blocks", frame)

    tool = SimpleNamespace(name="ReadFile")
    first = HumanMessage(content="go")
    state = SimpleNamespace(tool_context=SimpleNamespace(tools=(tool,)), messages=(first,))

    async def go() -> list[Any]:
        return [m async for m in ea._live_call_model("SYS")(state, SimpleNamespace(model="mx"))]

    out = asyncio.run(go())
    assert [m.content for m in out] == ["framed"]
    assert seen[0] == ("model", Config.CHAT_MODEL, {"model": "mx"})
    assert seen[1] == ("bind", [("lc", "ReadFile")])
    kind, messages = seen[2]
    assert kind == "stream" and messages[0].content == "SYS" and messages[1:] == [first]
    assert seen[3] == ("frame", "STREAM")
