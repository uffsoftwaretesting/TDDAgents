"""
The full runtime permission gate (Part C3), with the TDD phase rules as its managed tier.

Ported from `reference/claude-code/src/utils/permissions/permissions.ts` ->
`hasPermissionsToUseTool` (outer) and `hasPermissionsToUseToolInner` (inner), step for step.

**The TDD managed tier sits above step 1a.** `check_tdd_phase_permission` runs before any
rule, mode or hook, keyed on the ledger rather than on settings, so nothing loaded from a
settings file — not a project `allow`, not `bypassPermissions` — can reach past it. That is
the equivalent of upstream's managed-rules-only option, built into code. It **fails
closed**: if the check itself raises, the call is denied.

Upstream steps not ported, each because the mechanism is absent here: the sandbox
auto-allow exception at 1b, the auto-mode classifier and denial tracking in the outer gate,
and the PowerShell guard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.loop.permissions.messages import (
    DENIAL_WORKAROUND_GUIDANCE,
    auto_reject_message,
    create_permission_request_message,
    dont_ask_reject_message,
)
from app.loop.permissions.permission_request import (
    run_permission_request_hooks_for_headless_agent,
)
from app.loop.permissions.rules import (
    extract_permission_context,
    get_allow_rule_for_tool,
    get_ask_rule_for_tool,
    get_deny_rule_for_tool,
    is_ask_rule_reason,
    is_safety_check_ask,
    requires_user_interaction,
    tdd_phase_denial,
    tool_permission_result,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
)

if TYPE_CHECKING:
    from app.loop.context.tool_context import ToolContext
    from app.loop.tools.base import Tool

__all__ = [
    "DENIAL_WORKAROUND_GUIDANCE",
    "auto_reject_message",
    "has_permissions_to_use_tool",
    "resolve_ask_without_prompt",
]


async def has_permissions_to_use_tool(
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
    tool_use_id: str = "",
) -> PermissionResult:
    """
    `hasPermissionsToUseTool`, the outer gate: the inner decision; in auto mode an allow
    resets the denial streak and an 'ask' (not denied by `dontAsk`) goes to the classifier
    (`auto_mode.py`) before the headless deny; any other 'ask' is resolved by
    `resolve_ask_without_prompt`.
    """
    from app.loop.permissions.auto_mode import (
        auto_mode_decision,
        reset_denials_on_allow,
    )

    result = await _has_permissions_to_use_tool_inner(tool, input_args, context)
    perm_ctx = extract_permission_context(context)
    if result.behavior == PermissionBehavior.ALLOW:
        reset_denials_on_allow(context, perm_ctx)
        return result
    if result.behavior == PermissionBehavior.ASK and perm_ctx.mode == PermissionMode.AUTO:
        return await auto_mode_decision(result, tool, input_args, context, perm_ctx)
    return await resolve_ask_without_prompt(result, tool, input_args, context, tool_use_id)


async def resolve_ask_without_prompt(
    result: PermissionResult,
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
    tool_use_id: str = "",
) -> PermissionResult:
    """
    The outer gate's handling of an 'ask', split out so a PreToolUse hook's own 'ask'
    (upstream's `forceDecision`) goes through it too. Only an 'ask' is transformed, in this
    order: `dontAsk` denies; a context that cannot prompt (`should_avoid_permission_prompts`)
    gives PermissionRequest hooks the first word and otherwise denies. Anything else is
    returned unchanged — an interactive caller shows its prompt.
    """
    if result.behavior != PermissionBehavior.ASK:
        return result
    perm_ctx = extract_permission_context(context)
    if perm_ctx.mode == PermissionMode.DONT_ASK:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=dont_ask_reject_message(tool.name),
            decision_reason={"type": "mode", "mode": PermissionMode.DONT_ASK},
        )
    if perm_ctx.should_avoid_permission_prompts:
        hook_decision = await run_permission_request_hooks_for_headless_agent(
            tool, input_args, tool_use_id, context, perm_ctx.mode
        )
        if hook_decision is not None:
            return hook_decision
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=auto_reject_message(tool.name),
            decision_reason={"type": "asyncAgent", "reason": "Permission prompts are not available in this context"},
        )
    return result


def _updated_input_or_fallback(result: PermissionResult, input_args: dict[str, Any]) -> dict[str, Any]:
    """`getUpdatedInputOrFallback`."""
    return result.updated_input if result.updated_input is not None else input_args


async def _has_permissions_to_use_tool_inner(
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
) -> PermissionResult:
    """
    `hasPermissionsToUseToolInner`, after the TDD tier:

    1a. a tool-wide deny rule denies; 1b. a tool-wide ask rule asks;
    1c. the tool's own `check_permissions` (an exception leaves it at passthrough);
    1d. its deny stands; 1e. an ask from a tool that requires interaction stands;
    1f. an ask from a content ask rule stands; 1g. an ask from a safety check stands —
        all four before any mode, so bypass cannot skip them;
    2a. `bypassPermissions` (or `plan` when bypass is available) allows;
    2b. a tool-wide allow rule allows;
    3.  passthrough becomes ask.
    """
    if context.cancel.cancelled:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message="Operation cancelled.",
            decision_reason={"type": "other", "reason": "cancelled"},
        )

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

    tool_perm_res = await tool_permission_result(tool, input_args, context)

    if tool_perm_res.behavior == PermissionBehavior.DENY:
        return tool_perm_res

    if tool_perm_res.behavior == PermissionBehavior.ASK and requires_user_interaction(tool, input_args):
        return tool_perm_res
    if is_ask_rule_reason(tool_perm_res) or is_safety_check_ask(tool_perm_res):
        return tool_perm_res

    mode = perm_ctx.mode
    should_bypass = mode == PermissionMode.BYPASS_PERMISSIONS or (
        mode == PermissionMode.PLAN and perm_ctx.is_bypass_permissions_mode_available
    )
    if should_bypass:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=_updated_input_or_fallback(tool_perm_res, input_args),
            decision_reason={"type": "mode", "mode": mode},
        )

    allow_rule = get_allow_rule_for_tool(perm_ctx, tool.name)
    if allow_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=_updated_input_or_fallback(tool_perm_res, input_args),
            decision_reason={"type": "rule", "rule": allow_rule},
        )

    if tool_perm_res.behavior == PermissionBehavior.PASSTHROUGH:
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=create_permission_request_message(tool.name, tool_perm_res.decision_reason),
            updated_input=tool_perm_res.updated_input,
            decision_reason=tool_perm_res.decision_reason,
        )
    return tool_perm_res
