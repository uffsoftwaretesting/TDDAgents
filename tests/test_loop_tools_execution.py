"""
Tests for run_tool_use and yield_missing_tool_results (Parts B4 & B7).
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, ToolMessage

from app.loop.context import AppStateStore, tool_context_for
from app.loop.messages import ToolCall
from app.loop.tools.base import build_tool
from app.loop.tools.execution import (
    CANCEL_MESSAGE,
    SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
    run_tool_use,
    yield_missing_tool_results,
)
from app.loop.tools.types import (
    PermissionResult,
    ToolResult,
    ValidationResult,
)


class TestRunToolUse:
    """Part B4: run_tool_use execution path."""

    def test_run_tool_use_happy_path(self):
        async def go():
            tool = build_tool(
                name="read_file",
                prompt="p",
                call=lambda args, ctx: ToolResult(content=f"content of {args['path']}"),
            )
            ctx = tool_context_for(AppStateStore())
            asst_msg = AIMessage(content="reading")
            call: ToolCall = {"name": "read_file", "args": {"path": "test.txt"}, "id": "call_1"}

            outcome = await run_tool_use(call, asst_msg, ctx, [tool])
            assert isinstance(outcome.message, ToolMessage)
            assert outcome.message.content == "content of test.txt"
            assert outcome.message.tool_call_id == "call_1"
            assert outcome.message.status == "success"
            assert outcome.context_modifier is None

        asyncio.run(go())

    def test_run_tool_use_cancelled_context(self):
        async def go():
            called = False

            def do_call(args, ctx):
                nonlocal called
                called = True
                return ToolResult(content="done")

            tool = build_tool(name="read_file", prompt="p", call=do_call)
            ctx = tool_context_for(AppStateStore())
            ctx.cancel.cancel()
            asst_msg = AIMessage(content="reading")
            call: ToolCall = {"name": "read_file", "args": {}, "id": "call_1"}

            outcome = await run_tool_use(call, asst_msg, ctx, [tool])
            assert called is False
            assert outcome.message.content == CANCEL_MESSAGE
            assert outcome.message.status == "error"
            assert outcome.message.tool_call_id == "call_1"
            assert outcome.message.additional_kwargs == {"is_error": True}

        asyncio.run(go())

    def test_run_tool_use_unknown_tool(self):
        async def go():
            ctx = tool_context_for(AppStateStore())
            asst_msg = AIMessage(content="run")
            call: ToolCall = {"name": "non_existent", "args": {}, "id": "call_1"}

            outcome = await run_tool_use(call, asst_msg, ctx, [])
            assert "No such tool available: non_existent" in outcome.message.content
            assert outcome.message.status == "error"
            assert outcome.message.tool_call_id == "call_1"
            assert outcome.message.additional_kwargs == {"is_error": True}

        asyncio.run(go())

    def test_run_tool_use_validation_failure(self):
        async def go():
            tool = build_tool(
                name="write_file",
                prompt="p",
                call=lambda a, c: ToolResult(content="written"),
                validate_input=lambda a, c: ValidationResult(
                    valid=False, message="path cannot be empty", error_code=400
                ),
            )
            ctx = tool_context_for(AppStateStore())
            asst_msg = AIMessage(content="write")
            call: ToolCall = {"name": "write_file", "args": {"path": ""}, "id": "call_1"}

            outcome = await run_tool_use(call, asst_msg, ctx, [tool])
            assert "path cannot be empty" in outcome.message.content
            assert outcome.message.status == "error"
            assert outcome.message.additional_kwargs == {"is_error": True, "error_code": 400}

        asyncio.run(go())

    def test_run_tool_use_permission_denied(self):
        async def go():
            called = False

            def do_call(a, c):
                nonlocal called
                called = True
                return ToolResult(content="done")

            tool = build_tool(
                name="bash",
                prompt="p",
                call=do_call,
                check_permissions=lambda a, c: PermissionResult(
                    behavior="deny" if a and a.get("command") == "rm -rf /" else "allow",
                    message="rm -rf is denied",
                ),
            )
            ctx = tool_context_for(AppStateStore())
            asst_msg = AIMessage(content="bash")
            call: ToolCall = {"name": "bash", "args": {"command": "rm -rf /"}, "id": "call_1"}

            outcome = await run_tool_use(call, asst_msg, ctx, [tool])
            assert called is False
            assert "rm -rf is denied" in outcome.message.content
            assert outcome.message.status == "error"
            assert outcome.message.additional_kwargs == {"is_error": True, "permission_behavior": "deny"}

        asyncio.run(go())

    def test_run_tool_use_permission_updated_input(self):
        async def go():
            received_args = {}

            def do_call(a, c):
                nonlocal received_args
                received_args = a
                return ToolResult(content="done")

            tool = build_tool(
                name="bash",
                prompt="p",
                call=do_call,
                check_permissions=lambda a, c: PermissionResult(behavior="allow", updated_input={"command": "ls -la"}),
            )
            ctx = tool_context_for(AppStateStore())
            asst_msg = AIMessage(content="bash")
            call: ToolCall = {"name": "bash", "args": {"command": "ls"}, "id": "call_1"}

            outcome = await run_tool_use(call, asst_msg, ctx, [tool])
            assert received_args == {"command": "ls -la"}
            assert outcome.message.status == "success"

        asyncio.run(go())

    def test_run_tool_use_call_exception_caught(self):
        async def go():
            def explode(a, c):
                raise FileNotFoundError("missing.txt not found")

            tool = build_tool(name="read_file", prompt="p", call=explode)
            ctx = tool_context_for(AppStateStore())
            asst_msg = AIMessage(content="read")
            call: ToolCall = {"name": "read_file", "args": {}, "id": "call_1"}

            outcome = await run_tool_use(call, asst_msg, ctx, [tool])
            assert "Error calling tool (read_file): missing.txt not found" in outcome.message.content
            assert outcome.message.status == "error"

        asyncio.run(go())

    def test_run_tool_use_with_context_modifier(self):
        async def go():
            def modifier(ctx):
                return ctx

            tool = build_tool(
                name="set_flag",
                prompt="p",
                call=lambda a, c: ToolResult(content="flag set", context_modifier=modifier),
            )
            ctx = tool_context_for(AppStateStore())
            asst_msg = AIMessage(content="set")
            call: ToolCall = {"name": "set_flag", "args": {}, "id": "call_mod"}

            outcome = await run_tool_use(call, asst_msg, ctx, [tool])
            assert outcome.context_modifier is not None
            assert outcome.context_modifier.tool_use_id == "call_mod"
            assert outcome.context_modifier.modify_context is modifier

        asyncio.run(go())

    def test_run_tool_use_with_hook_stopped(self):
        async def go():
            tool = build_tool(
                name="stop_tool",
                prompt="p",
                call=lambda a, c: ToolResult(content="stop now", hook_stopped_continuation=True),
            )
            ctx = tool_context_for(AppStateStore())
            asst_msg = AIMessage(content="stop")
            call: ToolCall = {"name": "stop_tool", "args": {}, "id": "call_stop"}

            outcome = await run_tool_use(call, asst_msg, ctx, [tool])
            assert outcome.message.additional_kwargs.get("hook_stopped_continuation") is True

        asyncio.run(go())


class TestYieldMissingToolResults:
    """Part B7: History-repair invariant."""

    def test_yield_missing_when_all_completed(self):
        calls: list[ToolCall] = [
            {"name": "t1", "args": {}, "id": "c1"},
            {"name": "t2", "args": {}, "id": "c2"},
        ]
        completed = {"c1", "c2"}
        results = list(yield_missing_tool_results(calls, completed))
        assert len(results) == 0

    def test_yield_missing_generates_synthetic_error_results(self):
        calls: list[ToolCall] = [
            {"name": "t1", "args": {}, "id": "c1"},
            {"name": "t2", "args": {}, "id": "c2"},
            {"name": "t3", "args": {}, "id": "c3"},
        ]
        completed = {"c1"}
        results = list(yield_missing_tool_results(calls, completed))
        assert len(results) == 2
        assert results[0].tool_call_id == "c2"
        assert results[0].content == SYNTHETIC_TOOL_RESULT_PLACEHOLDER
        assert results[0].status == "error"
        assert results[0].additional_kwargs.get("is_error") is True
        assert results[0].additional_kwargs.get("synthetic_repair") is True

        assert results[1].tool_call_id == "c3"
        assert results[1].content == SYNTHETIC_TOOL_RESULT_PLACEHOLDER
        assert results[1].status == "error"

    def test_yield_missing_when_cancelled(self):
        calls: list[ToolCall] = [
            {"name": "t1", "args": {}, "id": "c1"},
            {"name": "t2", "args": {}, "id": "c2"},
        ]
        completed: set[str] = set()
        results = list(yield_missing_tool_results(calls, completed, cancelled=True))
        assert len(results) == 2
        assert results[0].content == CANCEL_MESSAGE
        assert results[1].content == CANCEL_MESSAGE

    def test_yield_missing_with_custom_message(self):
        calls: list[ToolCall] = [
            {"name": "t1", "args": {}, "id": "c1"},
        ]
        completed: set[str] = set()
        results = list(yield_missing_tool_results(calls, completed, message="Custom abort reason"))
        assert len(results) == 1
        assert results[0].content == "Custom abort reason"

    def test_yield_missing_with_empty_tool_id(self):
        calls: list[ToolCall] = [
            {"name": "t1", "args": {}, "id": ""},
        ]
        completed = {""}
        results = list(yield_missing_tool_results(calls, completed))
        assert len(results) == 0

        calls_missing: list[ToolCall] = [
            {"name": "t1", "args": {}, "id": ""},
        ]
        results_missing = list(yield_missing_tool_results(calls_missing, set()))
        assert len(results_missing) == 1
        assert results_missing[0].tool_call_id == ""
