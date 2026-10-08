"""
Rule-based permission evaluation (Part C2).

Ported from:
- `reference/claude-code/src/utils/permissions/permissions.ts`
  -> `checkRuleBasedPermissions`, `getDenyRuleForTool`, `getAskRuleForTool`
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Sequence

from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionResult,
    PermissionRule,
    ToolPermissionContext,
)

if TYPE_CHECKING:
    from app.loop.context import ToolContext
    from app.loop.tools.base import Tool


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


async def check_rule_based_permissions(
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
) -> PermissionResult | None:
    """
    Check rule-based permissions for a tool call (Part C2).

    Returns a PermissionResult if a rule explicitly denies or requires asking, or None if
    there is no rule-based objection.

    Precedence order:
    1a. Blanket deny rule on the entire tool
    1b. Blanket ask rule on the entire tool
    1c. Tool-specific check_permissions (e.g. bash subcommand or path rules)
    1d. Tool implementation denied -> return deny
    1f. Content-specific ask rule -> return ask
    1g. Bypass-immune safety check -> return ask
    """
    perm_ctx = extract_permission_context(context)

    # 1a. Entire tool is denied by rule
    deny_rule = get_deny_rule_for_tool(perm_ctx, tool.name)
    if deny_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Permission to use {tool.name} has been denied.",
            decision_reason={"type": "rule", "rule": deny_rule},
        )

    # 1b. Entire tool has an ask rule
    ask_rule = get_ask_rule_for_tool(perm_ctx, tool.name)
    if ask_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=f"Permission to use {tool.name} requires confirmation.",
            decision_reason={"type": "rule", "rule": ask_rule},
        )

    # 1c. Ask the tool implementation for a permission check
    tool_perm_res: PermissionResult
    try:
        res = tool.check_permissions(input_args, context)
        if asyncio.iscoroutine(res):
            tool_perm_res = await res
        else:
            tool_perm_res = res  # type: ignore[assignment]
    except Exception as e:
        # Fail closed on permission check error
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Permission check raised an exception: {e}",
            decision_reason={"type": "other", "reason": "exception"},
        )

    # 1d. Tool implementation denied permission
    if tool_perm_res.behavior == PermissionBehavior.DENY:
        return tool_perm_res

    # 1e. Tool requires user interaction even in bypass mode
    requires_interaction_fn = getattr(tool, "requires_user_interaction", None)
    if callable(requires_interaction_fn):
        try:
            req = requires_interaction_fn(input_args)
        except TypeError:
            req = requires_interaction_fn()
        if req and tool_perm_res.behavior == PermissionBehavior.ASK:
            return tool_perm_res

    # 1f. Content-specific ask rules from tool.checkPermissions
    if (
        tool_perm_res.behavior == PermissionBehavior.ASK
        and tool_perm_res.decision_reason
        and tool_perm_res.decision_reason.get("type") == "rule"
    ):
        return tool_perm_res

    # 1g. Bypass-immune safety checks (e.g. sensitive directories)
    if (
        tool_perm_res.behavior == PermissionBehavior.ASK
        and tool_perm_res.decision_reason
        and tool_perm_res.decision_reason.get("type") == "safetyCheck"
    ):
        return tool_perm_res

    # 1h. Preserve updated input from tool check_permissions
    if tool_perm_res.updated_input is not None:
        return tool_perm_res

    # No rule-based objection
    return None
