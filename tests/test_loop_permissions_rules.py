"""
Tests for rule-based permissions evaluation (Part C2).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.state.app_state_store import AppStateStore
from app.tool import PermissionResult, ToolResult, build_tool, tool_context_for
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    ToolPermissionContext,
)
from app.utils.permissions.permissions import (
    check_rule_based_permissions,
    extract_permission_context,
    get_allow_rule_for_tool,
    get_ask_rule_for_tool,
    get_deny_rule_for_tool,
    mcp_info_from_string,
    rule_matches,
    tool_name_matches_rule,
)


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
            ctx.permission_context = perm_ctx

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
            ctx.permission_context = perm_ctx

            res = await check_rule_based_permissions(tool, {}, ctx)
            assert res is not None
            assert res.behavior == PermissionBehavior.ASK
            assert res.message == "The agent requested permissions to use Careful, but you haven't granted it yet."
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

    def test_requires_user_interaction_is_left_to_the_caller(self):
        """Upstream: "Caller must pre-check tool.requiresUserInteraction() — step 1e is not replicated"."""
        async def go():
            tool = build_tool(
                name="PromptUser",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok"),
                check_permissions=lambda a, c: PermissionResult(behavior="ask", message="needs input"),
                requires_user_interaction=lambda args: True,
            )
            ctx = tool_context_for(AppStateStore())
            assert await check_rule_based_permissions(tool, {}, ctx) is None

        asyncio.run(go())

    def test_plain_ask_and_passthrough_and_updated_input_are_no_objection(self):
        async def go():
            ctx = tool_context_for(AppStateStore())
            for result in (
                PermissionResult(behavior="ask", message="m"),
                PermissionResult(behavior="ask", decision_reason={"type": "workingDir", "reason": "x"}),
                PermissionResult(behavior="passthrough"),
                PermissionResult(behavior="allow", updated_input={"x": 1}),
            ):
                def check(a: Any, c: Any, r: PermissionResult = result) -> PermissionResult:
                    return r

                tool = build_tool(name="T", prompt="p", call=lambda a, c: ToolResult(content="ok"),
                                  check_permissions=check)
                assert await check_rule_based_permissions(tool, {}, ctx) is None, result

        asyncio.run(go())

    def test_rule_reason_from_a_non_ask_rule_is_no_objection(self):
        async def go():
            allow_rule = PermissionRule(tool_name="Bash", rule_content="x", rule_behavior=PermissionBehavior.ALLOW)
            tool = build_tool(name="Bash", prompt="p", call=lambda a, c: ToolResult(content="ok"),
                              check_permissions=lambda a, c: PermissionResult(
                                  behavior="ask", decision_reason={"type": "rule", "rule": allow_rule}))
            assert await check_rule_based_permissions(tool, {}, tool_context_for(AppStateStore())) is None

        asyncio.run(go())

    def test_exception_in_check_permissions_is_passthrough(self, caplog):
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
            assert await check_rule_based_permissions(tool, {}, ctx) is None

        asyncio.run(go())

    def test_phase_tier_runs_first(self):
        async def go():
            from app.state.app_state_store import AppState
            from app.tdd.ledger import PhaseLedger, TddPhase

            tool = build_tool(name="WriteImplementation", prompt="p", call=lambda a, c: ToolResult(content="ok"))
            ctx = tool_context_for(AppStateStore(state=AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED))))
            res = await check_rule_based_permissions(tool, {}, ctx)
            assert res is not None and res.behavior == PermissionBehavior.DENY
            assert (res.decision_reason or {})["type"] == "phase_rule"

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


# ── tool-name matching (upstream toolMatchesRule / mcpInfoFromString) ────────

def test_plain_prefix_is_not_a_match():
    """The old `startswith` check let a `Bash` rule cover `BashOutput`."""
    assert rule_matches(PermissionRule(tool_name="Bash"), "BashOutput") is False
    assert tool_name_matches_rule("Bash", "BashOutput") is False
    assert tool_name_matches_rule("Bash", "Bash") is True
    assert tool_name_matches_rule("", "Bash") is False


def test_mcp_server_rules():
    assert tool_name_matches_rule("mcp__srv", "mcp__srv__read") is True
    assert tool_name_matches_rule("mcp__srv__*", "mcp__srv__read") is True
    assert tool_name_matches_rule("mcp__srv__read", "mcp__srv__write") is False
    assert tool_name_matches_rule("mcp__srv", "mcp__srvx__read") is False
    assert tool_name_matches_rule("mcp__srv", "Bash") is False
    assert tool_name_matches_rule("Bash", "mcp__srv__read") is False


@pytest.mark.parametrize("value, expected", [
    ("mcp__srv__tool", ("srv", "tool")),
    ("mcp__srv__a__b", ("srv", "a__b")),
    ("mcp__srv", ("srv", None)),
    ("mcp__", None),
    ("mcp", None),
    ("other__srv__tool", None),
    ("mcp____tool", None),
])
def test_mcp_info_from_string(value, expected):
    assert mcp_info_from_string(value) == expected


def test_extract_ignores_a_non_context_value_on_app_state():
    ctx = SimpleNamespace(permission_context=None,
                          get_app_state=lambda: SimpleNamespace(tool_permission_context="garbage"))
    assert extract_permission_context(ctx) == ToolPermissionContext()  # type: ignore[arg-type]
