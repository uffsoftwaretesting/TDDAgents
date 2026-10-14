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

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.loop.permissions.messages import (
    DENIAL_WORKAROUND_GUIDANCE,
    auto_reject_message,
    create_permission_request_message,
    dont_ask_reject_message,
)
from app.loop.permissions.permission_request import run_permission_request_hooks_for_headless_agent
from app.loop.permissions.rules import (
    extract_permission_context,
    get_allow_rule_for_tool,
    get_ask_rule_for_tool,
    get_deny_rule_for_tool,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
)

if TYPE_CHECKING:
    from app.loop.context import ToolContext
    from app.loop.tools.base import Tool

logger = logging.getLogger("TDDOrchestrator.Permissions")


__all__ = [
    "DENIAL_WORKAROUND_GUIDANCE",
    "auto_reject_message",
    "has_permissions_to_use_tool",
]


async def has_permissions_to_use_tool(
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
    tool_use_id: str = "",
) -> PermissionResult:
    """
    `hasPermissionsToUseTool`, the outer gate. Only an 'ask' is transformed, and in this
    order: `dontAsk` denies; a context that cannot prompt (`should_avoid_permission_prompts`)
    gives PermissionRequest hooks the first word and otherwise denies.
    """
    result = await _has_permissions_to_use_tool_inner(tool, input_args, context)
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


def _check_tdd_phase(tool: Tool, input_args: dict[str, Any], context: ToolContext) -> PermissionResult | None:
    """
    The managed tier: a deny when the ledger's phase forbids this call, else `None`.

    Fails closed — a check that raises is a refusal, because a phase rule that silently
    stops applying is barrier 2 switched off.
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


def _requires_user_interaction(tool: Tool, input_args: dict[str, Any]) -> bool:
    fn = getattr(tool, "requires_user_interaction", None)
    if not callable(fn):
        return False
    try:
        return bool(fn(input_args))
    except TypeError:
        return bool(fn())


def _is_ask_rule_reason(result: PermissionResult) -> bool:
    reason = result.decision_reason or {}
    rule = reason.get("rule")
    return (
        reason.get("type") == "rule"
        and isinstance(rule, PermissionRule)
        and rule.rule_behavior == PermissionBehavior.ASK
    )


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

    phase_denial = _check_tdd_phase(tool, input_args, context)
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

    # Upstream seeds this with a request message; step 3 rebuilds the message for every
    # passthrough, so the seed's is never read and is not reproduced.
    tool_perm_res = PermissionResult(behavior=PermissionBehavior.PASSTHROUGH)
    try:
        res = tool.check_permissions(input_args, context)
        if asyncio.iscoroutine(res):
            res = await res
        if isinstance(res, PermissionResult):
            tool_perm_res = res
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Permission check of %s raised; treating it as passthrough", tool.name)

    if tool_perm_res.behavior == PermissionBehavior.DENY:
        return tool_perm_res

    if tool_perm_res.behavior == PermissionBehavior.ASK:
        if _requires_user_interaction(tool, input_args):
            return tool_perm_res
        if _is_ask_rule_reason(tool_perm_res):
            return tool_perm_res
        if (tool_perm_res.decision_reason or {}).get("type") == "safetyCheck":
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
