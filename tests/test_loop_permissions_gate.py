"""
Tests for the full runtime permission gate (Part C3).
"""

from __future__ import annotations

import asyncio

from app.loop.context import AppStateStore, tool_context_for
from app.loop.permissions.gate import has_permissions_to_use_tool
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    ToolPermissionContext,
)
from app.loop.tools.base import build_tool
from app.loop.tools.types import PermissionResult, ToolResult


class TestRuntimePermissionGate:
    def test_cancelled_context_denies_immediately(self):
        async def go():
            tool = build_tool(name="tool", prompt="p", call=lambda a, c: ToolResult(content="ok"))
            ctx = tool_context_for(AppStateStore())
            ctx.cancel.cancel()

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.DENY
            assert res.message == "Operation cancelled."
            assert res.decision_reason == {"type": "other", "reason": "cancelled"}

        asyncio.run(go())

    def test_deny_rule_takes_precedence_over_bypass_mode(self):
        """Precedence: deny rules sit ABOVE every allow path, including bypass mode."""
        async def go():
            tool = build_tool(name="Forbidden", prompt="p", call=lambda a, c: ToolResult(content="ok"))
            rule = PermissionRule(tool_name="Forbidden", rule_behavior=PermissionBehavior.DENY)
            perm_ctx = ToolPermissionContext(
                mode=PermissionMode.BYPASS_PERMISSIONS,
                always_deny_rules=(rule,),
            )
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.DENY
            assert res.message == "Permission to use Forbidden has been denied."
            assert res.decision_reason == {"type": "rule", "rule": rule}

        asyncio.run(go())

    def test_interaction_required_tool_asks_even_in_bypass_mode(self):
        """Interaction-required tools prompt even in bypass mode."""
        async def go():
            tool = build_tool(
                name="AskQuestion",
                prompt="p",
                call=lambda a, c: ToolResult(content="answered"),
                check_permissions=lambda a, c: PermissionResult(behavior="ask", message="Requires interaction"),
                requires_user_interaction=lambda a: True,
            )
            perm_ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ASK
            assert res.message == "Requires interaction"

        asyncio.run(go())

    def test_interaction_required_fallback_when_tool_perm_allows(self):
        """When tool check_permissions returns allow or None, requires_user_interaction falls back to ask."""
        async def go():
            tool = build_tool(
                name="InteractiveNoCheck",
                prompt="p",
                call=lambda a, c: ToolResult(content="answered"),
                requires_user_interaction=lambda a: True,
            )
            perm_ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ASK
            assert res.message == "Tool 'InteractiveNoCheck' requires user interaction."
            assert res.decision_reason == {"type": "userInteraction"}

        asyncio.run(go())

    def test_content_specific_ask_rule_is_bypass_immune(self):
        """Content-specific ask rules prompt even in bypass mode."""
        async def go():
            rule = PermissionRule(tool_name="Bash", rule_behavior=PermissionBehavior.ASK, rule_content="npm publish:*")
            tool = build_tool(
                name="Bash",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(
                    behavior="ask",
                    message="Publish requires confirmation",
                    decision_reason={"type": "rule", "rule": rule},
                ),
            )
            perm_ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {"command": "npm publish:package"}, ctx)
            assert res.behavior == PermissionBehavior.ASK
            assert res.message == "Publish requires confirmation"
            assert res.decision_reason == {"type": "rule", "rule": rule}

        asyncio.run(go())

    def test_safety_check_asks_even_in_bypass_mode(self):
        """Safety checks (bypass-immune) prompt even in bypass mode."""
        async def go():
            tool = build_tool(
                name="Edit",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(
                    behavior="ask",
                    message="Sensitive directory access",
                    decision_reason={"type": "safetyCheck", "reason": "git dir"},
                ),
            )
            perm_ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ASK
            assert res.decision_reason == {"type": "safetyCheck", "reason": "git dir"}

        asyncio.run(go())

    def test_bypass_mode_auto_allows_standard_tools(self):
        async def go():
            tool = build_tool(name="Bash", prompt="p", call=lambda a, c: ToolResult(content="ok"))
            perm_ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ALLOW
            assert res.decision_reason == {"type": "mode", "mode": PermissionMode.BYPASS_PERMISSIONS}

        asyncio.run(go())

    def test_plan_mode_with_bypass_available_auto_allows(self):
        async def go():
            tool = build_tool(name="tool", prompt="p", call=lambda a, c: ToolResult(content="ok"))
            perm_ctx = ToolPermissionContext(
                mode=PermissionMode.PLAN,
                is_bypass_permissions_mode_available=True,
            )
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ALLOW
            assert res.decision_reason == {"type": "mode", "mode": PermissionMode.PLAN}

        asyncio.run(go())

    def test_plan_mode_read_only_allowed_mutating_asks(self):
        async def go():
            read_tool = build_tool(
                name="Read", prompt="p", call=lambda a, c: ToolResult(content="ok"), is_read_only=lambda a: True
            )
            write_tool = build_tool(
                name="Write", prompt="p", call=lambda a, c: ToolResult(content="ok"), is_read_only=lambda a: False
            )

            perm_ctx = ToolPermissionContext(
                mode=PermissionMode.PLAN,
                is_bypass_permissions_mode_available=False,
            )
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            # Read-only tool allowed in plan mode
            res_read = await has_permissions_to_use_tool(read_tool, {}, ctx)
            assert res_read.behavior == PermissionBehavior.ALLOW
            assert res_read.decision_reason == {"type": "mode", "mode": PermissionMode.PLAN}

            # Mutating tool asks in plan mode
            res_write = await has_permissions_to_use_tool(write_tool, {}, ctx)
            assert res_write.behavior == PermissionBehavior.ASK
            assert res_write.message == "Modifying operations like 'Write' require confirmation in plan mode."
            assert res_write.decision_reason == {"type": "mode", "mode": PermissionMode.PLAN}

        asyncio.run(go())

    def test_accept_edits_mode_allows_read_only(self):
        async def go():
            tool = build_tool(
                name="Inspect", prompt="p", call=lambda a, c: ToolResult(content="ok"), is_read_only=lambda a: True
            )
            perm_ctx = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ALLOW
            assert res.decision_reason == {"type": "mode", "mode": PermissionMode.ACCEPT_EDITS}

        asyncio.run(go())

    def test_always_allow_rule_allows_tool(self):
        async def go():
            tool = build_tool(name="CustomTool", prompt="p", call=lambda a, c: ToolResult(content="ok"))
            rule = PermissionRule(tool_name="CustomTool", rule_behavior=PermissionBehavior.ALLOW)
            perm_ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT, always_allow_rules=(rule,))
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ALLOW
            assert res.decision_reason == {"type": "rule", "rule": rule}

        asyncio.run(go())

    def test_blanket_ask_rule_in_gate(self):
        async def go():
            tool = build_tool(name="AskGateTool", prompt="p", call=lambda a, c: ToolResult(content="ok"))
            ask_rule = PermissionRule(tool_name="AskGateTool", rule_behavior=PermissionBehavior.ASK)
            perm_ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT, always_ask_rules=(ask_rule,))
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ASK
            assert res.message == "Permission to use AskGateTool requires confirmation."
            assert res.decision_reason == {"type": "rule", "rule": ask_rule}

        asyncio.run(go())

    def test_check_permissions_exception_in_gate(self):
        async def go():
            def explode(a, c):
                raise RuntimeError("boom")

            tool = build_tool(
                name="Exploding",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=explode,
            )
            ctx = tool_context_for(AppStateStore())
            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.DENY
            assert res.message == "Permission check error: boom"
            assert res.decision_reason == {"type": "other", "reason": "exception"}

        asyncio.run(go())

    def test_is_read_only_exception_handled_safely(self):
        async def go():
            def explode_read(a):
                raise ValueError("corrupt args")

            tool = build_tool(
                name="Fragile",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                is_read_only=explode_read,
                check_permissions=lambda a, c: PermissionResult(behavior="passthrough"),
            )
            perm_ctx = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            # In acceptEdits mode, non-read-only falls through to passthrough -> ask
            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ASK
            assert res.message == "Permission requested for Fragile."
            assert res.decision_reason == {"type": "other", "reason": "passthrough"}

        asyncio.run(go())

    def test_passthrough_converts_to_ask_in_default_mode(self):
        async def go():
            tool = build_tool(
                name="Tool",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(behavior="passthrough", message="check me"),
            )
            ctx = tool_context_for(AppStateStore())
            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ASK
            assert res.message == "check me"
            assert res.decision_reason == {"type": "other", "reason": "passthrough"}

            tool_no_msg = build_tool(
                name="NoMsgTool",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(behavior="passthrough"),
            )
            res2 = await has_permissions_to_use_tool(tool_no_msg, {}, ctx)
            assert res2.behavior == PermissionBehavior.ASK
            assert res2.message == "Permission requested for NoMsgTool."
            assert res2.decision_reason == {"type": "other", "reason": "passthrough"}

        asyncio.run(go())

    def test_dont_ask_mode_converts_passthrough_and_ask_to_deny(self):
        async def go():
            tool_pass = build_tool(
                name="T1", prompt="p", call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(behavior="passthrough"),
            )
            tool_ask = build_tool(
                name="T2", prompt="p", call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(behavior="ask"),
            )

            perm_ctx = ToolPermissionContext(mode=PermissionMode.DONT_ASK)
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res1 = await has_permissions_to_use_tool(tool_pass, {}, ctx)
            assert res1.behavior == PermissionBehavior.DENY
            assert res1.message == "Permission prompt suppressed in dontAsk mode for T1."
            assert res1.decision_reason == {"type": "mode", "mode": PermissionMode.DONT_ASK}

            res2 = await has_permissions_to_use_tool(tool_ask, {}, ctx)
            assert res2.behavior == PermissionBehavior.DENY
            assert res2.message == "Permission prompt suppressed in dontAsk mode for T2."
            assert res2.decision_reason == {"type": "mode", "mode": PermissionMode.DONT_ASK}

        asyncio.run(go())

    def test_preserve_updated_input(self):
        async def go():
            tool = build_tool(
                name="Tool",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(behavior="allow", updated_input={"cleaned": True}),
            )
            perm_ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ALLOW
            assert res.updated_input == {"cleaned": True}
            assert res.decision_reason == {"type": "mode", "mode": PermissionMode.BYPASS_PERMISSIONS}

        asyncio.run(go())

    def test_always_allow_rule_permits_in_default_mode(self):
        async def go():
            tool = build_tool(
                name="SpecialTool",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                is_read_only=lambda a: False,
            )
            allow_rule = PermissionRule(tool_name="SpecialTool", rule_behavior=PermissionBehavior.ALLOW)
            perm_ctx = ToolPermissionContext(
                mode=PermissionMode.DEFAULT,
                always_allow_rules=(allow_rule,),
            )
            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ALLOW
            assert res.decision_reason == {"type": "rule", "rule": allow_rule}

        asyncio.run(go())

    def test_async_check_permissions_coroutine(self):
        async def go():
            async def async_perm(a, c):
                await asyncio.sleep(0.001)
                return PermissionResult(behavior="allow")

            tool = build_tool(
                name="AsyncTool",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=async_perm,
            )
            ctx = tool_context_for(AppStateStore())
            res = await has_permissions_to_use_tool(tool, {}, ctx)
            assert res.behavior == PermissionBehavior.ALLOW

        asyncio.run(go())
