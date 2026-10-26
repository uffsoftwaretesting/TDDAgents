"""
Rule-based permission evaluation (Part C2).

Ported from:
- `reference/claude-code/src/utils/permissions/permissions.ts`
  -> `checkRuleBasedPermissions`, `getDenyRuleForTool`, `getAskRuleForTool`
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Sequence

from app.loop.permissions.messages import create_permission_request_message
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionResult,
    PermissionRule,
    ToolPermissionContext,
)

if TYPE_CHECKING:
    from app.loop.context.tool_context import ToolContext
    from app.loop.tools.base import Tool

logger = logging.getLogger("TDDOrchestrator.Permissions")


def mcp_info_from_string(tool_string: str) -> tuple[str, str | None] | None:
    """
    `mcpStringUtils.ts` -> `mcpInfoFromString`: `mcp__server__tool` -> ("server", "tool").

    Everything after the server name is the tool name, double underscores preserved.
    """
    parts = tool_string.split("__")
    if len(parts) < 2 or parts[0] != "mcp" or not parts[1]:
        return None
    tool = "__".join(parts[2:]) if len(parts) > 2 else None
    return parts[1], tool


def tool_name_matches_rule(rule_tool_name: str, tool_name: str) -> bool:
    """
    The name half of upstream `toolMatchesRule`: an exact name, or an MCP server rule
    (`mcp__server` or `mcp__server__*`) matching every tool on that server. A plain prefix
    is not a match: a `Bash` rule must not cover `BashOutput`.
    """
    if rule_tool_name == tool_name:
        return True
    rule_info = mcp_info_from_string(rule_tool_name)
    tool_info = mcp_info_from_string(tool_name)
    return (
        rule_info is not None
        and tool_info is not None
        and rule_info[1] in (None, "*")
        and rule_info[0] == tool_info[0]
    )


def rule_matches(rule: PermissionRule, tool_name: str, input_content: str | None = None) -> bool:
    """
    Check if a permission rule matches a tool call.

    The tool name must match as `tool_name_matches_rule` defines it. If rule.rule_content
    is defined, input_content must match (exact match or glob-style prefix with '*').
    """
    if not tool_name_matches_rule(rule.tool_name, tool_name):
        return False

    if rule.rule_content is None:
        # Blanket rule for the entire tool
        return True

    if input_content is None:
        return False

    if rule.rule_content.endswith("*"):
        prefix = rule.rule_content[:-1]
        return input_content.startswith(prefix)

    return rule.rule_content == input_content


def get_rule_for_tool(
    rules: Sequence[PermissionRule],
    tool_name: str,
    input_content: str | None = None,
) -> PermissionRule | None:
    """
    Find the first rule in the sequence that matches the tool and optional content.
    """
    for rule in rules:
        if rule_matches(rule, tool_name, input_content):
            return rule
    return None


def get_deny_rule_for_tool(
    context: ToolPermissionContext,
    tool_name: str,
    input_content: str | None = None,
) -> PermissionRule | None:
    """Check if the tool or content is matched by an always-deny rule."""
    return get_rule_for_tool(context.always_deny_rules, tool_name, input_content)


def get_ask_rule_for_tool(
    context: ToolPermissionContext,
    tool_name: str,
    input_content: str | None = None,
) -> PermissionRule | None:
    """Check if the tool or content is matched by an always-ask rule."""
    return get_rule_for_tool(context.always_ask_rules, tool_name, input_content)


def get_allow_rule_for_tool(
    context: ToolPermissionContext,
    tool_name: str,
    input_content: str | None = None,
) -> PermissionRule | None:
    """Check if the tool or content is matched by an always-allow rule."""
    return get_rule_for_tool(context.always_allow_rules, tool_name, input_content)


def extract_permission_context(context: ToolContext) -> ToolPermissionContext:
    """
    Extract ToolPermissionContext from ToolContext or AppState, falling back to default.
    """
    if hasattr(context, "permission_context") and isinstance(context.permission_context, ToolPermissionContext):
        return context.permission_context

    if hasattr(context, "get_app_state"):
        try:
            app_state = context.get_app_state()
            if hasattr(app_state, "tool_permission_context") and isinstance(
                app_state.tool_permission_context, ToolPermissionContext
            ):
                return app_state.tool_permission_context
        except Exception:
            pass

    return ToolPermissionContext()


def tdd_phase_denial(tool: Tool, input_args: dict[str, Any], context: ToolContext) -> PermissionResult | None:
    """
    The TDD managed tier: a deny when the ledger's phase forbids this call, else `None`.

    It runs before every rule, mode and hook — in the gate and in the rule-only re-check of
    a hook verdict alike — so that no allow path can reach past it. It fails closed: a check
    that raises is a refusal, because a phase rule that silently stops applying is barrier 2
    switched off.
    """
    try:
        ledger = getattr(context.get_app_state(), "phase_ledger", None)
        if ledger is None:
            return None
        from app.loop.permissions.tdd import check_tdd_phase_permission

        permitted, reason = check_tdd_phase_permission(tool, input_args, ledger)
    except Exception as exc:
        logger.exception("TDD phase check failed for %s", tool.name)
        reason = f"TDD phase check failed for {tool.name}: {exc}"
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=reason,
            decision_reason={"type": "phase_rule", "phase": None, "reason": reason},
        )
    if permitted:
        return None
    return PermissionResult(
        behavior=PermissionBehavior.DENY,
        message=reason,
        decision_reason={"type": "phase_rule", "phase": ledger.phase, "reason": reason},
    )


def requires_user_interaction(tool: Tool, input_args: dict[str, Any]) -> bool:
    """`tool.requiresUserInteraction?.()`, for predicates with or without the input."""
    fn = getattr(tool, "requires_user_interaction", None)
    if not callable(fn):
        return False
    try:
        return bool(fn(input_args))
    except TypeError:
        return bool(fn())


def is_ask_rule_reason(result: PermissionResult) -> bool:
    """Step 1f's test: an 'ask' whose reason is a rule whose own behavior is 'ask'."""
    reason = result.decision_reason or {}
    rule = reason.get("rule")
    return (
        result.behavior == PermissionBehavior.ASK
        and reason.get("type") == "rule"
        and isinstance(rule, PermissionRule)
        and rule.rule_behavior == PermissionBehavior.ASK
    )


def is_safety_check_ask(result: PermissionResult) -> bool:
    """Step 1g's test: an 'ask' raised by a bypass-immune safety check."""
    return result.behavior == PermissionBehavior.ASK and (result.decision_reason or {}).get("type") == "safetyCheck"


async def tool_permission_result(tool: Tool, input_args: dict[str, Any], context: ToolContext) -> PermissionResult:
    """
    Step 1c: the tool's own `check_permissions`. Upstream seeds a passthrough and keeps it
    when the check throws (logged, not a deny); cancellation propagates.
    """
    result = PermissionResult(behavior=PermissionBehavior.PASSTHROUGH)
    try:
        res = tool.check_permissions(input_args, context)
        if asyncio.iscoroutine(res):
            res = await res
        if isinstance(res, PermissionResult):
            result = res
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Permission check of %s raised; treating it as passthrough", tool.name)
    return result


async def check_rule_based_permissions(
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
) -> PermissionResult | None:
    """
    `checkRuleBasedPermissions`: only the rule-based steps of the pipeline — the subset
    `bypassPermissions` respects — preceded by the TDD managed tier.

    Returns a deny or an ask, or `None` when no rule objects. It deliberately does **not**
    run step 1e (the caller checks `requires_user_interaction`), any mode step, always-allow
    rules, passthrough-to-ask, dontAsk, the headless deny or PermissionRequest hooks. It is
    what a PreToolUse hook's verdict is re-checked against.

    0.  TDD phase tier -> deny
    1a. tool-wide deny rule -> deny
    1b. tool-wide ask rule -> ask
    1c. the tool's own check; 1d. its deny -> deny
    1f. an ask from a content ask rule -> ask
    1g. an ask from a safety check -> ask
    """
    phase_denial = tdd_phase_denial(tool, input_args, context)
    if phase_denial is not None:
        return phase_denial

    perm_ctx = extract_permission_context(context)

    deny_rule = get_deny_rule_for_tool(perm_ctx, tool.name)
    if deny_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Permission to use {tool.name} has been denied.",
            decision_reason={"type": "rule", "rule": deny_rule},
        )

    ask_rule = get_ask_rule_for_tool(perm_ctx, tool.name)
    if ask_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=create_permission_request_message(tool.name),
            decision_reason={"type": "rule", "rule": ask_rule},
        )

    result = await tool_permission_result(tool, input_args, context)
    if result.behavior == PermissionBehavior.DENY:
        return result
    if is_ask_rule_reason(result) or is_safety_check_ask(result):
        return result
    return None
