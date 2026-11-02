"""Callback hooks (an SDK host's, `registerHookCallbacks`) and function hooks (`sessionHooks.ts`)."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage

from app.schemas.hooks import CallbackHook, CommandHook, FunctionHook, HookMatcher
from app.tool import CancelToken
from app.utils.hooks.hooks import hook_display_text
from app.utils.messages import HookBlockingError
from tests.test_loop_hooks_execute import PRE, collect, dispatcher

STOP = {"hook_event_name": "Stop", "stop_hook_active": False}


def register(d: Any, event: str, *hooks: Any, matcher: str | None = None) -> None:
    d.register_hook_callbacks({event: [HookMatcher(hooks=tuple(hooks), matcher=matcher)]})


def test_a_callback_hook_answers_as_a_command_hook_would() -> None:
    seen: list[Any] = []

    async def callback(hook_input: Any, tool_use_id: str, signal: Any) -> dict[str, Any]:
        seen.append((hook_input, tool_use_id))
        return {"decision": "block", "reason": "host says no"}

    d = dispatcher()
    register(d, "PreToolUse", CallbackHook(callback=callback), matcher="Bash")
    assert d.has_hook_for_event("PreToolUse") and not d.has_hook_for_event("Stop")
    results = collect(d.execute_hooks(PRE, "tu", match_query="Bash"))
    assert seen == [(PRE, "tu")]
    blocking = [r.blocking_error for r in results if r.blocking_error is not None]
    assert blocking == [HookBlockingError(blocking_error="host says no", command="callback")]


def test_a_callback_that_answers_async_is_a_plain_success_and_a_matcher_filters() -> None:
    calls: list[str] = []

    async def asynchronous(hook_input: Any, tool_use_id: str, signal: Any) -> dict[str, Any]:
        calls.append(tool_use_id)
        return {"async": True}

    d = dispatcher()
    register(d, "PreToolUse", CallbackHook(callback=asynchronous), matcher="Edit")
    assert collect(d.execute_hooks(PRE, "tu", match_query="Bash")) == []
    register(d, "PreToolUse", CallbackHook(callback=asynchronous))
    assert collect(d.execute_hooks(PRE, "tu", match_query="Bash")) == []
    assert calls == ["tu"]


def test_registered_hooks_run_even_under_a_managed_only_policy() -> None:
    from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot

    async def callback(hook_input: Any, tool_use_id: str, signal: Any) -> dict[str, Any]:
        return {"systemMessage": "hi"}

    d = dispatcher(HooksConfigSnapshot(managed_only=True))
    register(d, "Stop", CallbackHook(callback=callback))
    results = collect(d.execute_hooks(STOP, "tu", messages=[]))
    assert "hi" in [r.attachment.content for r in results if r.attachment is not None]


def function_hook(callback: Any, timeout: float | None = None) -> FunctionHook:
    return FunctionHook(callback=callback, error_message="You MUST call the tool.", id="f1", timeout=timeout)


def run_function(hook: FunctionHook, messages: Any = (), **kwargs: Any) -> list[Any]:
    d = dispatcher()
    d.session_matchers["Stop"] = [HookMatcher(hooks=(hook,))]
    return collect(d.execute_hooks(STOP, "tu", messages=list(messages) if messages is not None else None,
                                   **kwargs))


def test_a_function_hook_passes_or_blocks_with_its_message() -> None:
    seen: list[Any] = []

    def judge(messages: Any) -> bool:
        seen.append(messages)
        return any(getattr(m, "content", "") == "done" for m in messages)

    assert run_function(function_hook(judge), [HumanMessage(content="done")]) == []
    results = run_function(function_hook(judge), [HumanMessage(content="nope")])
    assert [r.blocking_error for r in results if r.blocking_error is not None] == [
        HookBlockingError(blocking_error="You MUST call the tool.", command="function")]
    assert [m.content for m in seen[0]] == ["done"]


def test_an_awaitable_function_hook_and_its_timeout() -> None:
    async def slow(messages: Any) -> bool:
        await asyncio.sleep(1)
        return True

    async def quick(messages: Any) -> bool:
        return False

    assert run_function(function_hook(slow, timeout=10)) == []
    assert run_function(function_hook(quick))[0].blocking_error is not None


def test_a_function_hook_without_messages_or_failing_is_a_non_blocking_error() -> None:
    def broken(messages: Any) -> bool:
        raise ValueError("bad judge")

    (missing,) = run_function(function_hook(lambda m: True), None)
    assert missing.attachment is not None and missing.attachment.type == "hook_error_during_execution"
    assert missing.attachment.content == "Messages not provided for function hook"
    (failed,) = run_function(function_hook(broken))
    assert failed.attachment is not None and failed.attachment.content == "bad judge"
    token = CancelToken()
    token.cancel()
    assert run_function(function_hook(lambda m: False), signal=token) == []


def test_display_text_and_outside_the_repl() -> None:
    async def callback(hook_input: Any, tool_use_id: str, signal: Any) -> dict[str, Any]:
        return {"decision": "block", "systemMessage": "stopped"}

    async def failing(hook_input: Any, tool_use_id: str, signal: Any) -> dict[str, Any]:
        raise RuntimeError("host gone")

    async def asynchronous(hook_input: Any, tool_use_id: str, signal: Any) -> dict[str, Any]:
        return {"async": True}

    assert hook_display_text(CallbackHook(callback=callback)) == "callback"
    assert hook_display_text(function_hook(lambda m: True)) == "function"
    assert hook_display_text(CommandHook(command="x")) == "x"
    d = dispatcher()
    register(d, "SessionEnd", CallbackHook(callback=callback), CallbackHook(callback=failing),
             CallbackHook(callback=asynchronous))
    d.session_matchers["SessionEnd"] = [HookMatcher(hooks=(function_hook(lambda m: True),))]
    results = asyncio.run(d.execute_hooks_outside_repl({"hook_event_name": "SessionEnd"}))
    assert [(r.command, r.succeeded, r.output, r.blocked) for r in results] == [
        ("callback", True, "stopped", True), ("callback", False, "host gone", False),
        ("callback", True, "", False),
        ("function", False, "Internal error: function hook executed outside REPL context", False)]
