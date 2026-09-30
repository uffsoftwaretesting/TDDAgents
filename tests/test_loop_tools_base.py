"""
Tests for Tool protocol, BuiltTool, and build_tool with fail-closed defaults (Parts B1 & B2).
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import ToolMessage

from app.loop.context import AppStateStore, tool_context_for
from app.loop.tools.base import (
    BuiltTool,
    Tool,
    build_tool,
    default_map_result,
    find_tool_by_name,
    tool_matches_name,
)
from app.loop.tools.types import (
    ContextModifier,
    PermissionResult,
    ToolResult,
    ValidationResult,
)


class TestToolTypes:
    """Part B1: Core dataclasses."""

    def test_validation_result_defaults(self):
        vr = ValidationResult(valid=True)
        assert vr.valid is True
        assert vr.message == ""
        assert vr.error_code == 0

    def test_validation_result_custom(self):
        vr = ValidationResult(valid=False, message="invalid argument", error_code=400)
        assert vr.valid is False
        assert vr.message == "invalid argument"
        assert vr.error_code == 400

    def test_permission_result_defaults(self):
        pr = PermissionResult()
        assert pr.behavior == "allow"
        assert pr.updated_input is None
        assert pr.message == ""

    def test_permission_result_deny(self):
        pr = PermissionResult(behavior="deny", message="Not allowed")
        assert pr.behavior == "deny"
        assert pr.message == "Not allowed"

    def test_permission_result_updated_input(self):
        pr = PermissionResult(behavior="allow", updated_input={"fixed": True})
        assert pr.updated_input == {"fixed": True}

    def test_tool_result_defaults(self):
        tr = ToolResult(content="output")
        assert tr.content == "output"
        assert tr.is_error is False
        assert tr.tool_use_id == ""
        assert tr.system_reminder is None
        assert tr.hook_stopped_continuation is False
        assert tr.context_modifier is None

    def test_tool_result_with_context_modifier(self):
        def modifier(ctx):
            return ctx

        tr = ToolResult(content="ok", context_modifier=modifier)
        assert tr.context_modifier is modifier

    def test_context_modifier(self):
        def modifier_fn(ctx):
            return ctx

        cm = ContextModifier(tool_use_id="tu_1", modify_context=modifier_fn)
        assert cm.tool_use_id == "tu_1"
        assert cm.modify_context is modifier_fn


class TestBuildToolFailClosedDefaults:
    """Part B2: build_tool factory with fail-closed defaults."""

    def test_built_tool_satisfies_protocol(self):
        tool = build_tool(
            name="test_tool",
            prompt="A test tool",
            call=lambda args, ctx: ToolResult(content="done"),
        )
        assert isinstance(tool, Tool)
        assert isinstance(tool, BuiltTool)
        assert tool.name == "test_tool"
        assert tool.prompt == "A test tool"

    def test_defaults_are_fail_closed(self):
        tool = build_tool(
            name="test_tool",
            prompt="prompt",
            call=lambda args, ctx: ToolResult(content="ok"),
        )
        assert tool.is_concurrency_safe({}) is False
        assert tool.is_read_only({}) is False
        assert tool.is_destructive({}) is False
        assert tool.is_enabled() is True
        assert tool.description({}) == "test_tool"
        assert tool.input_schema == {"type": "object"}
        assert tool.aliases == ()
        assert tool.is_mcp is False

    def test_validate_input_defaults_to_valid(self):
        tool = build_tool(
            name="test_tool",
            prompt="prompt",
            call=lambda args, ctx: ToolResult(content="ok"),
        )
        ctx = tool_context_for(AppStateStore())
        vr = tool.validate_input({"arg": 1}, ctx)
        assert vr.valid is True
        assert vr.message == ""

    def test_check_permissions_defaults_to_allow(self):
        async def go():
            tool = build_tool(
                name="test_tool",
                prompt="prompt",
                call=lambda args, ctx: ToolResult(content="ok"),
            )
            ctx = tool_context_for(AppStateStore())
            pr = await tool.check_permissions({}, ctx)
            assert pr.behavior == "allow"

        asyncio.run(go())

    def test_is_concurrency_safe_exception_guard(self):
        def explode(args):
            raise RuntimeError("quote parse failure")

        tool = build_tool(
            name="exploding",
            prompt="p",
            call=lambda a, c: ToolResult(content="ok"),
            is_concurrency_safe=explode,
        )
        # Fail-closed: must return False rather than propagating exception
        assert tool.is_concurrency_safe({}) is False

    def test_is_read_only_exception_guard(self):
        def explode(args):
            raise ValueError("bad check")

        tool = build_tool(
            name="exploding",
            prompt="p",
            call=lambda a, c: ToolResult(content="ok"),
            is_read_only=explode,
        )
        # Fail-closed: must return False rather than propagating exception
        assert tool.is_read_only({}) is False

    def test_is_destructive_exception_guard(self):
        def explode(args):
            raise KeyError("missing key")

        tool = build_tool(
            name="exploding",
            prompt="p",
            call=lambda a, c: ToolResult(content="ok"),
            is_destructive=explode,
        )
        assert tool.is_destructive({}) is False

    def test_is_enabled_exception_guard(self):
        def explode():
            raise RuntimeError("config check failed")

        tool = build_tool(
            name="exploding",
            prompt="p",
            call=lambda a, c: ToolResult(content="ok"),
            is_enabled=explode,
        )
        assert tool.is_enabled() is False

    def test_validate_input_exception_guard(self):
        def explode(args, ctx):
            raise ValueError("Schema validation crashed")

        tool = build_tool(
            name="exploding",
            prompt="p",
            call=lambda a, c: ToolResult(content="ok"),
            validate_input=explode,
        )
        ctx = tool_context_for(AppStateStore())
        res = tool.validate_input({}, ctx)
        assert res.valid is False
        assert "Schema validation crashed" in res.message

    def test_sync_and_async_call_execution(self):
        async def go():
            sync_tool = build_tool(
                name="sync_tool",
                prompt="p",
                call=lambda args, ctx: ToolResult(content="sync_result"),
            )
            ctx = tool_context_for(AppStateStore())
            res1 = await sync_tool.call({}, ctx)
            assert res1.content == "sync_result"

            async def async_call(args, ctx):
                return ToolResult(content="async_result")

            async_tool = build_tool(
                name="async_tool",
                prompt="p",
                call=async_call,
            )
            res2 = await async_tool.call({}, ctx)
            assert res2.content == "async_result"

        asyncio.run(go())

    def test_sync_and_async_check_permissions(self):
        async def go():
            sync_tool = build_tool(
                name="sync_perm",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(behavior="deny", message="no"),
            )
            ctx = tool_context_for(AppStateStore())
            res1 = await sync_tool.check_permissions({}, ctx)
            assert res1.behavior == "deny"
            assert res1.message == "no"

            async def async_perm(a, c):
                return PermissionResult(behavior="allow", updated_input={"a": 1})

            async_tool = build_tool(
                name="async_perm",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=async_perm,
            )
            res2 = await async_tool.check_permissions({}, ctx)
            assert res2.behavior == "allow"
            assert res2.updated_input == {"a": 1}

        asyncio.run(go())


class TestMapResult:
    """Mapping ToolResult to LangChain ToolMessage."""

    def test_default_map_result_success(self):
        tr = ToolResult(content="File created", is_error=False)
        msg = default_map_result(tr, "call_123")
        assert isinstance(msg, ToolMessage)
        assert msg.content == "File created"
        assert msg.tool_call_id == "call_123"
        assert msg.status == "success"
        assert msg.additional_kwargs == {}

    def test_default_map_result_error(self):
        tr = ToolResult(content="File not found", is_error=True)
        msg = default_map_result(tr, "call_456")
        assert msg.content == "File not found"
        assert msg.tool_call_id == "call_456"
        assert msg.status == "error"
        assert msg.additional_kwargs.get("is_error") is True

    def test_default_map_result_hook_stopped_and_system_reminder(self):
        tr = ToolResult(
            content="Stopped",
            hook_stopped_continuation=True,
            system_reminder="Remember to write tests",
        )
        msg = default_map_result(tr, "call_789")
        assert msg.additional_kwargs["hook_stopped_continuation"] is True
        assert msg.additional_kwargs["system_reminder"] == "Remember to write tests"


class TestToolLookup:
    """Tool matching and finding by name and alias."""

    def test_tool_matches_name(self):
        tool = build_tool(
            name="TaskStop",
            prompt="p",
            call=lambda a, c: ToolResult(content=""),
            aliases=("KillShell", "StopTask"),
        )
        assert tool_matches_name(tool, "TaskStop") is True
        assert tool_matches_name(tool, "KillShell") is True
        assert tool_matches_name(tool, "StopTask") is True
        assert tool_matches_name(tool, "Other") is False

    def test_find_tool_by_name_primary_first(self):
        t1 = build_tool(name="ToolA", prompt="p", call=lambda a, c: ToolResult(content=""), aliases=("ToolB",))
        t2 = build_tool(name="ToolB", prompt="p", call=lambda a, c: ToolResult(content=""))
        tools = [t1, t2]

        # Primary name ToolB matches t2, not t1's alias
        assert find_tool_by_name(tools, "ToolB") is t2
        assert find_tool_by_name(tools, "ToolA") is t1

    def test_find_tool_by_name_fallback_to_alias(self):
        t1 = build_tool(name="TaskStop", prompt="p", call=lambda a, c: ToolResult(content=""), aliases=("KillShell",))
        tools = [t1]
        assert find_tool_by_name(tools, "KillShell") is t1
        assert find_tool_by_name(tools, "NonExistent") is None

    def test_tool_without_aliases_attribute(self):
        class SimpleTool:
            name = "simple"

        simple = SimpleTool()
        assert tool_matches_name(simple, "simple") is True  # type: ignore[arg-type]
        assert tool_matches_name(simple, "other") is False  # type: ignore[arg-type]
        assert find_tool_by_name([simple], "simple") is simple  # type: ignore[comparison-overlap,list-item]
        assert find_tool_by_name([simple], "other") is None  # type: ignore[list-item]
