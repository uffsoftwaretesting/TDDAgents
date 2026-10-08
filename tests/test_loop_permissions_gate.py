"""
Tests for the full runtime permission gate (Part C3).
"""

from __future__ import annotations

import asyncio

from app.loop.context import AppStateStore, tool_context_for
from app.loop.permissions.gate import (
    DENIAL_WORKAROUND_GUIDANCE,
    auto_reject_message,
    has_permissions_to_use_tool,
)
from app.loop.ledger import PhaseLedger, TddPhase
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


# ── outer gate: dontAsk and headless (upstream hasPermissionsToUseTool) ──────

def _gate(tool, perm_ctx):
    ctx = tool_context_for(AppStateStore(), permission_context=perm_ctx)
    return asyncio.run(has_permissions_to_use_tool(tool, {}, ctx))


def _asking_tool(name="Asker", behavior=PermissionBehavior.PASSTHROUGH):
    return build_tool(name=name, prompt="p", call=lambda a, c: ToolResult(content="ok"),
                      check_permissions=lambda a, c: PermissionResult(behavior=behavior, message="m"))


def test_auto_reject_message_text():
    assert DENIAL_WORKAROUND_GUIDANCE.startswith("IMPORTANT: You *may* attempt to accomplish this action")
    assert DENIAL_WORKAROUND_GUIDANCE.endswith("Let the user decide how to proceed.")
    assert auto_reject_message("Bash") == f"Permission to use Bash has been denied. {DENIAL_WORKAROUND_GUIDANCE}"


def test_headless_context_denies_unresolved_asks():
    perm = ToolPermissionContext(should_avoid_permission_prompts=True)
    for behavior in (PermissionBehavior.PASSTHROUGH, PermissionBehavior.ASK):
        res = _gate(_asking_tool(behavior=behavior), perm)
        assert res.behavior == PermissionBehavior.DENY
        assert res.message == auto_reject_message("Asker")
        assert res.decision_reason == {"type": "asyncAgent",
                                       "reason": "Permission prompts are not available in this context"}


def test_interactive_context_keeps_the_ask():
    res = _gate(_asking_tool(), ToolPermissionContext(should_avoid_permission_prompts=False))
    assert res.behavior == PermissionBehavior.ASK
    assert res.message == "m"


def test_headless_does_not_touch_allow_or_deny():
    perm = ToolPermissionContext(should_avoid_permission_prompts=True)
    assert _gate(_asking_tool(behavior=PermissionBehavior.ALLOW), perm).behavior == PermissionBehavior.ALLOW
    denied = _gate(_asking_tool(behavior=PermissionBehavior.DENY), perm)
    assert denied.behavior == PermissionBehavior.DENY
    assert denied.message == "m"


def test_dont_ask_wins_over_headless():
    perm = ToolPermissionContext(mode=PermissionMode.DONT_ASK, should_avoid_permission_prompts=True)
    res = _gate(_asking_tool(), perm)
    assert res.behavior == PermissionBehavior.DENY
    assert res.message == "Permission prompt suppressed in dontAsk mode for Asker."
    assert res.decision_reason == {"type": "mode", "mode": PermissionMode.DONT_ASK}


def test_should_avoid_permission_prompts_defaults_off():
    assert ToolPermissionContext().should_avoid_permission_prompts is False


# ── mutation-driven pins: pass-through of context, input and results ────────

class _Tool:
    """A duck-typed tool: sync check_permissions, configurable predicates."""

    def __init__(self, name="T", result=None, read_only=lambda a: False, interaction=None):
        self.name = name
        self._result = result if result is not None else PermissionResult(behavior=PermissionBehavior.PASSTHROUGH)
        self._read_only = read_only
        self.seen_ctx = None
        self.seen_input = None
        if interaction is not None:
            self.requires_user_interaction = interaction

    def check_permissions(self, input_args, context):
        self.seen_ctx = context
        return self._result

    def is_read_only(self, input_args):
        self.seen_input = input_args
        return self._read_only(input_args)


def _run_gate(tool, perm=None, args=None, ctx=None):
    ctx = ctx or tool_context_for(AppStateStore(), permission_context=perm or ToolPermissionContext())
    return asyncio.run(has_permissions_to_use_tool(tool, args if args is not None else {}, ctx))


def test_phase_rule_decision_reason_is_exact():
    store = AppStateStore()
    store.update(lambda s: s.__class__(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
    ctx = tool_context_for(store)
    tool = build_tool(name="WriteImplementation", prompt="p", call=lambda a, c: ToolResult(content="x"))
    res = asyncio.run(has_permissions_to_use_tool(tool, {}, ctx))
    assert res.behavior == PermissionBehavior.DENY
    assert res.decision_reason == {"type": "phase_rule", "phase": TddPhase.RED, "reason": res.message}


def test_sync_check_permissions_result_and_context_are_used():
    tool = _Tool(result=PermissionResult(behavior=PermissionBehavior.DENY, message="nope"))
    ctx = tool_context_for(AppStateStore())
    res = _run_gate(tool, ctx=ctx)
    assert (res.behavior, res.message) == (PermissionBehavior.DENY, "nope")
    assert tool.seen_ctx is ctx


def test_zero_arg_requires_user_interaction_is_honored():
    tool = _Tool(interaction=lambda: True)
    res = _run_gate(tool, ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS))
    assert res.behavior == PermissionBehavior.ASK
    assert res.decision_reason == {"type": "userInteraction"}


def test_tool_without_requires_user_interaction_is_fine():
    tool = _Tool(result=PermissionResult(behavior=PermissionBehavior.ALLOW))
    assert not hasattr(tool, "requires_user_interaction")
    assert _run_gate(tool).behavior == PermissionBehavior.ALLOW


def test_allow_with_rule_reason_does_not_short_circuit_plan_mode():
    reason = {"type": "rule", "rule": "r"}
    tool = _Tool(result=PermissionResult(behavior=PermissionBehavior.ALLOW, decision_reason=reason))
    res = _run_gate(tool, ToolPermissionContext(mode=PermissionMode.PLAN, is_bypass_permissions_mode_available=False))
    assert res.behavior == PermissionBehavior.ASK


def test_allow_with_safety_reason_does_not_short_circuit_plan_mode():
    reason = {"type": "safetyCheck", "reason": "x"}
    tool = _Tool(result=PermissionResult(behavior=PermissionBehavior.ALLOW, decision_reason=reason))
    res = _run_gate(tool, ToolPermissionContext(mode=PermissionMode.PLAN, is_bypass_permissions_mode_available=False))
    assert res.behavior == PermissionBehavior.ASK


def test_updated_input_is_carried_through_every_allow_path():
    upd = {"command": "rewritten"}
    base = PermissionResult(behavior=PermissionBehavior.PASSTHROUGH, updated_input=upd)
    rule = PermissionRule(tool_name="T", rule_behavior=PermissionBehavior.ALLOW)
    cases = [
        ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS),
        ToolPermissionContext(always_allow_rules=(rule,)),
        ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS),
        ToolPermissionContext(mode=PermissionMode.PLAN, is_bypass_permissions_mode_available=False),
    ]
    for perm in cases:
        res = _run_gate(_Tool(result=base, read_only=lambda a: True), perm)
        assert res.behavior == PermissionBehavior.ALLOW, perm
        assert res.updated_input == upd, perm
    asked = _run_gate(_Tool(result=base), cases[3])
    assert asked.behavior == PermissionBehavior.ASK
    assert asked.updated_input == upd


def test_read_only_predicate_gets_the_input_and_failures_are_not_reads():
    tool = _Tool(read_only=lambda a: a.get("ro") is True)
    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
    assert _run_gate(tool, perm, {"ro": True}).behavior == PermissionBehavior.ALLOW
    assert tool.seen_input == {"ro": True}

    def boom(a):
        raise RuntimeError("x")

    plan = ToolPermissionContext(mode=PermissionMode.PLAN, is_bypass_permissions_mode_available=False)
    assert _run_gate(_Tool(read_only=boom), plan).behavior == PermissionBehavior.ASK
