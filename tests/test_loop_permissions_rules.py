"""
Tests for rule-based permissions evaluation (Part C2).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.loop.context import AppStateStore, tool_context_for
from app.loop.permissions.rules import (
    check_rule_based_permissions,
    extract_permission_context,
    get_allow_rule_for_tool,
    get_ask_rule_for_tool,
    get_deny_rule_for_tool,
    rule_matches,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    ToolPermissionContext,
)
from app.loop.tools.base import build_tool
from app.loop.tools.types import PermissionResult, ToolResult


class TestRuleMatching:
    def test_blanket_rule_matches_tool_name(self):
        rule = PermissionRule(tool_name="Bash")
        assert rule_matches(rule, "Bash") is True
        assert rule_matches(rule, "Bash", "ls -la") is True
        assert rule_matches(rule, "Edit") is False

    def test_mcp_prefix_rule_matches(self):
        rule = PermissionRule(tool_name="mcp__server")
        assert rule_matches(rule, "mcp__server__read") is True
        assert rule_matches(rule, "mcp__server") is True
        assert rule_matches(rule, "mcp__other__read") is False

    def test_exact_content_matching(self):
        rule = PermissionRule(tool_name="Bash", rule_content="rm -rf /")
        assert rule_matches(rule, "Bash", "rm -rf /") is True
        assert rule_matches(rule, "Bash", "ls") is False
        assert rule_matches(rule, "Bash", None) is False

    def test_wildcard_prefix_content_matching(self):
        rule = PermissionRule(tool_name="Bash", rule_content="npm publish:*")
        assert rule_matches(rule, "Bash", "npm publish:package") is True
        assert rule_matches(rule, "Bash", "npm test") is False
        assert rule_matches(rule, "Bash", None) is False

        # Single-character prefix rule kills mutant changing [:-1] to [:-2]
        rule_short = PermissionRule(tool_name="Bash", rule_content="a*")
        assert rule_matches(rule_short, "Bash", "apple") is True
        assert rule_matches(rule_short, "Bash", "banana") is False

    def test_rule_lookups(self):
        deny_rule = PermissionRule(tool_name="DangerousTool", rule_behavior=PermissionBehavior.DENY)
        deny_rule_content = PermissionRule(tool_name="Bash", rule_behavior=PermissionBehavior.DENY, rule_content="rm:*")
        ask_rule = PermissionRule(tool_name="Bash", rule_behavior=PermissionBehavior.ASK, rule_content="sudo:*")
        allow_rule = PermissionRule(tool_name="ReadFile", rule_behavior=PermissionBehavior.ALLOW)
        allow_rule_content = PermissionRule(
            tool_name="Bash", rule_behavior=PermissionBehavior.ALLOW, rule_content="echo:*"
        )

        ctx = ToolPermissionContext(
            always_deny_rules=(deny_rule, deny_rule_content),
            always_ask_rules=(ask_rule,),
            always_allow_rules=(allow_rule, allow_rule_content),
        )

        assert get_deny_rule_for_tool(ctx, "DangerousTool") is deny_rule
        assert get_deny_rule_for_tool(ctx, "Bash") is None
        assert get_deny_rule_for_tool(ctx, "Bash", "rm:foo") is deny_rule_content
        assert get_deny_rule_for_tool(ctx, "Bash", "ls") is None

        assert get_ask_rule_for_tool(ctx, "Bash", "sudo:restart") is ask_rule
        assert get_ask_rule_for_tool(ctx, "Bash", "ls") is None

        assert get_allow_rule_for_tool(ctx, "ReadFile") is allow_rule
        assert get_allow_rule_for_tool(ctx, "Other") is None
        assert get_allow_rule_for_tool(ctx, "Bash", "echo:hello") is allow_rule_content
        assert get_allow_rule_for_tool(ctx, "Bash", "other") is None


class TestCheckRuleBasedPermissions:
    def test_blanket_deny_rule_returns_deny(self):
        async def go():
            tool = build_tool(name="Blocked", prompt="p", call=lambda a, c: ToolResult(content="ok"))
            deny_rule = PermissionRule(tool_name="Blocked", rule_behavior=PermissionBehavior.DENY)
            perm_ctx = ToolPermissionContext(always_deny_rules=(deny_rule,))

            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await check_rule_based_permissions(tool, {}, ctx)
            assert res is not None
            assert res.behavior == PermissionBehavior.DENY
            assert "Permission to use Blocked has been denied" in res.message
            assert res.decision_reason == {"type": "rule", "rule": deny_rule}

        asyncio.run(go())

    def test_blanket_ask_rule_returns_ask(self):
        async def go():
            tool = build_tool(name="Careful", prompt="p", call=lambda a, c: ToolResult(content="ok"))
            ask_rule = PermissionRule(tool_name="Careful", rule_behavior=PermissionBehavior.ASK)
            perm_ctx = ToolPermissionContext(always_ask_rules=(ask_rule,))

            ctx = tool_context_for(AppStateStore())
            setattr(ctx, "permission_context", perm_ctx)

            res = await check_rule_based_permissions(tool, {}, ctx)
            assert res is not None
            assert res.behavior == PermissionBehavior.ASK
            assert "requires confirmation" in res.message
            assert res.decision_reason == {"type": "rule", "rule": ask_rule}

        asyncio.run(go())

    def test_tool_implementation_deny_returned(self):
        async def go():
            def check_fn(a, c):
                assert a == {"flag": 1}
                assert c is not None
                return PermissionResult(behavior="deny", message="tool-specific denial")

            tool = build_tool(
                name="tool",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=check_fn,
            )
            ctx = tool_context_for(AppStateStore())
            res = await check_rule_based_permissions(tool, {"flag": 1}, ctx)
            assert res is not None
            assert res.behavior == PermissionBehavior.DENY
            assert res.message == "tool-specific denial"

        asyncio.run(go())

    def test_tool_content_specific_ask_rule_returned(self):
        async def go():
            rule = PermissionRule(tool_name="Bash", rule_content="rm:*", rule_behavior=PermissionBehavior.ASK)
            tool = build_tool(
                name="Bash",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(
                    behavior="ask",
                    message="rm requires approval",
                    decision_reason={"type": "rule", "rule": rule},
                ),
            )
            ctx = tool_context_for(AppStateStore())
            res = await check_rule_based_permissions(tool, {}, ctx)
            assert res is not None
            assert res.behavior == PermissionBehavior.ASK
            assert res.decision_reason == {"type": "rule", "rule": rule}

        asyncio.run(go())

    def test_tool_safety_check_ask_returned(self):
        async def go():
            tool = build_tool(
                name="WriteFile",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(
                    behavior="ask",
                    message="sensitive path",
                    decision_reason={"type": "safetyCheck", "reason": "git config"},
                ),
            )
            ctx = tool_context_for(AppStateStore())
            res = await check_rule_based_permissions(tool, {}, ctx)
            assert res is not None
            assert res.behavior == PermissionBehavior.ASK
            assert res.decision_reason == {"type": "safetyCheck", "reason": "git config"}

        asyncio.run(go())

    def test_requires_user_interaction_in_check_rule_based_permissions(self):
        async def go():
            # 1. requires_user_interaction with input args returning True when behavior is ASK
            tool1 = build_tool(
                name="PromptUser",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(behavior="ask", message="needs input"),
                requires_user_interaction=lambda args: args.get("interactive") is True,
            )
            ctx = tool_context_for(AppStateStore())
            res1 = await check_rule_based_permissions(tool1, {"interactive": True}, ctx)
            assert res1 is not None
            assert res1.behavior == PermissionBehavior.ASK
            assert res1.message == "needs input"

            # 2. When interactive is False -> returns None
            res1_no = await check_rule_based_permissions(tool1, {"interactive": False}, ctx)
            assert res1_no is None

            # 3. When requires_user_interaction takes 0 args (TypeError fallback)
            tool2 = build_tool(
                name="ZeroArg",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(behavior="ask", message="needs zero"),
                requires_user_interaction=lambda: True,
            )
            res2 = await check_rule_based_permissions(tool2, {}, ctx)
            assert res2 is not None
            assert res2.behavior == PermissionBehavior.ASK

            # 4. When behavior is ALLOW, even if requires_user_interaction is True, does not return ask
            tool3 = build_tool(
                name="AllowTool",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(behavior="allow"),
                requires_user_interaction=lambda: True,
            )
            res3 = await check_rule_based_permissions(tool3, {}, ctx)
            assert res3 is None

        asyncio.run(go())

    def test_exception_in_check_permissions_fails_closed(self):
        async def go():
            def explode(a, c):
                raise RuntimeError("database unreachable")

            tool = build_tool(
                name="Exploding",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=explode,
            )
            ctx = tool_context_for(AppStateStore())
            res = await check_rule_based_permissions(tool, {}, ctx)
            assert res is not None
            assert res.behavior == PermissionBehavior.DENY
            assert res.message == "Permission check raised an exception: database unreachable"
            assert res.decision_reason == {"type": "other", "reason": "exception"}

        asyncio.run(go())

    def test_no_rule_objection_returns_none(self):
        async def go():
            tool = build_tool(name="Safe", prompt="p", call=lambda a, c: ToolResult(content="ok"))
            ctx = tool_context_for(AppStateStore())
            res = await check_rule_based_permissions(tool, {}, ctx)
            assert res is None

        asyncio.run(go())

    def test_extract_permission_context_fallbacks(self):
        # 1. Fallback when context has no permission_context attribute and no get_app_state
        raw_ctx = SimpleNamespace()
        perm = extract_permission_context(raw_ctx)  # type: ignore[arg-type]
        assert isinstance(perm, ToolPermissionContext)
        assert perm.mode == PermissionMode.DEFAULT

        # 2. Extract from context.get_app_state()
        perm_custom = ToolPermissionContext(mode=PermissionMode.DONT_ASK)
        state_mock = SimpleNamespace(tool_permission_context=perm_custom)
        ctx_with_app_state = SimpleNamespace(get_app_state=lambda: state_mock)
        extracted = extract_permission_context(ctx_with_app_state)  # type: ignore[arg-type]
        assert extracted is perm_custom
        assert extracted.mode == PermissionMode.DONT_ASK

        # 3. When get_app_state() raises an exception, safely falls back
        def exploding_state():
            raise RuntimeError("state failure")

        ctx_error = SimpleNamespace(get_app_state=exploding_state)
        extracted_err = extract_permission_context(ctx_error)  # type: ignore[arg-type]
        assert isinstance(extracted_err, ToolPermissionContext)
        assert extracted_err.mode == PermissionMode.DEFAULT
