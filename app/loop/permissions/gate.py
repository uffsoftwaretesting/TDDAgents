"""
The full runtime permission gate (Part C3).

Coordinates rule checking, mode ladders, bypass permissions, and passthrough->ask conversion.

Ported from:
- `reference/claude-code/src/utils/permissions/permissions.ts`
  -> `hasPermissionsToUseTool`, `hasPermissionsToUseToolInner`
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

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
)

if TYPE_CHECKING:
    from app.loop.context import ToolContext
    from app.loop.tools.base import Tool


#: `DENIAL_WORKAROUND_GUIDANCE` (`src/utils/messages.ts`).
DENIAL_WORKAROUND_GUIDANCE = (
    "IMPORTANT: You *may* attempt to accomplish this action using other tools that might naturally be used "
    "to accomplish this goal, e.g. using head instead of cat. But you *should not* attempt to work around "
    "this denial in malicious ways, e.g. do not use your ability to run tests to execute non-test actions. "
    "You should only try to work around this restriction in reasonable ways that do not attempt to bypass "
    "the intent behind this denial. If you believe this capability is essential to complete the user's "
    "request, STOP and explain to the user what you were trying to do and why you need this permission. "
    "Let the user decide how to proceed."
)


def auto_reject_message(tool_name: str) -> str:
    """`AUTO_REJECT_MESSAGE`."""
    return f"Permission to use {tool_name} has been denied. {DENIAL_WORKAROUND_GUIDANCE}"


async def has_permissions_to_use_tool(
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
) -> PermissionResult:
    """
    The outer gate, as upstream's `hasPermissionsToUseTool`: every 'ask' the inner gate
    produces becomes 'deny' in dontAsk mode, and then in a context that cannot show a
    prompt (`should_avoid_permission_prompts`, upstream's headless/background agents).

    Upstream runs PermissionRequest hooks before the headless deny; this repository's hook
    layer has no PermissionRequest decision protocol, so the deny is unconditional.
    """
    result = await _has_permissions_to_use_tool_inner(tool, input_args, context)
    if result.behavior != PermissionBehavior.ASK:
        return result
    perm_ctx = extract_permission_context(context)
    if perm_ctx.mode == PermissionMode.DONT_ASK:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Permission prompt suppressed in {perm_ctx.mode} mode for {tool.name}.",
            decision_reason={"type": "mode", "mode": perm_ctx.mode},
        )
    if perm_ctx.should_avoid_permission_prompts:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=auto_reject_message(tool.name),
            decision_reason={"type": "asyncAgent", "reason": "Permission prompts are not available in this context"},
        )
    return result


async def _has_permissions_to_use_tool_inner(
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
) -> PermissionResult:
    """
    Check if a tool call is authorized to execute (Part C3).

    Precedence order:
    1. Check cooperative cancellation (aborted context -> deny).
    2. Rule-based evaluation:
       - Blanket deny rule -> deny
       - Blanket ask rule -> ask
       - Tool implementation check_permissions:
         - exception -> deny
         - deny -> deny
         - requires_user_interaction -> ask (bypass-immune!)
         - content-specific ask rule -> ask (bypass-immune!)
         - safety check (e.g. sensitive directory) -> ask (bypass-immune!)
    3. Mode-based evaluation:
       - bypassPermissions mode (or plan mode with bypass available) -> allow
       - Always-allow rule -> allow
       - acceptEdits mode: read-only operations allow
       - plan mode: read-only operations allow, mutating operations ask
    4. Passthrough conversion: 'passthrough' becomes 'ask'.
    """
    # 1. Cooperative cancellation check
    if context.cancel.cancelled:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message="Operation cancelled.",
            decision_reason={"type": "other", "reason": "cancelled"},
        )

    # 1b. TDD phase authorization check (Part D4)
    # Sits above all allow paths (including bypass mode), enforcing Red->Green invariants.
    if hasattr(context, "get_app_state"):
        try:
            app_state = context.get_app_state()
            ledger = getattr(app_state, "phase_ledger", None)
            if ledger is not None:
                from app.loop.permissions.tdd import check_tdd_phase_permission

                permitted, reason = check_tdd_phase_permission(tool, input_args, ledger)
                if not permitted:
                    return PermissionResult(
                        behavior=PermissionBehavior.DENY,
                        message=reason,
                        decision_reason={"type": "phase_rule", "phase": ledger.phase, "reason": reason},
                    )
        except Exception:
            pass

    perm_ctx = extract_permission_context(context)

    # 2a. Blanket deny rule
    deny_rule = get_deny_rule_for_tool(perm_ctx, tool.name)
    if deny_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Permission to use {tool.name} has been denied.",
            decision_reason={"type": "rule", "rule": deny_rule},
        )

    # 2b. Blanket ask rule
    ask_rule = get_ask_rule_for_tool(perm_ctx, tool.name)
    if ask_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=f"Permission to use {tool.name} requires confirmation.",
            decision_reason={"type": "rule", "rule": ask_rule},
        )

    # 2c. Tool implementation permission check
    tool_perm_res: PermissionResult
    try:
        res = tool.check_permissions(input_args, context)
        if asyncio.iscoroutine(res):
            tool_perm_res = await res
        else:
            tool_perm_res = res  # type: ignore[assignment]
    except Exception as e:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Permission check error: {e}",
            decision_reason={"type": "other", "reason": "exception"},
        )

    # 2d. Tool implementation denied permission
    if tool_perm_res.behavior == PermissionBehavior.DENY:
        return tool_perm_res

    # 2e. Tools requiring user interaction are strictly bypass-immune
    requires_interaction_fn = getattr(tool, "requires_user_interaction", None)
    if callable(requires_interaction_fn):
        try:
            req = requires_interaction_fn(input_args)
        except TypeError:
            req = requires_interaction_fn()
        if req:
            if tool_perm_res.behavior == PermissionBehavior.ASK:
                return tool_perm_res
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                message=f"Tool '{tool.name}' requires user interaction.",
                decision_reason={"type": "userInteraction"},
            )

    # 2f. Content-specific ask rules from tool.checkPermissions take precedence over bypass
    if (
        tool_perm_res.behavior == PermissionBehavior.ASK
        and tool_perm_res.decision_reason
        and tool_perm_res.decision_reason.get("type") == "rule"
    ):
        return tool_perm_res

    # 2g. Safety checks are bypass-immune
    if (
        tool_perm_res.behavior == PermissionBehavior.ASK
        and tool_perm_res.decision_reason
        and tool_perm_res.decision_reason.get("type") == "safetyCheck"
    ):
        return tool_perm_res

    mode = perm_ctx.mode
    updated_input = tool_perm_res.updated_input

    # 3a. Bypass permissions mode
    should_bypass = (mode == PermissionMode.BYPASS_PERMISSIONS) or (
        mode == PermissionMode.PLAN and perm_ctx.is_bypass_permissions_mode_available
    )
    if should_bypass:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=updated_input,
            decision_reason={"type": "mode", "mode": mode},
        )

    # 3b. Always-allow rules
    allow_rule = get_allow_rule_for_tool(perm_ctx, tool.name)
    if allow_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=updated_input,
            decision_reason={"type": "rule", "rule": allow_rule},
        )

    # 3c. Read-only capability check for acceptEdits and plan modes
    try:
        is_read = bool(tool.is_read_only(input_args))
    except Exception:
        is_read = False

    if mode == PermissionMode.ACCEPT_EDITS:
        if is_read:
            return PermissionResult(
                behavior=PermissionBehavior.ALLOW,
                updated_input=updated_input,
                decision_reason={"type": "mode", "mode": mode},
            )

    elif mode == PermissionMode.PLAN:
        if is_read:
            return PermissionResult(
                behavior=PermissionBehavior.ALLOW,
                updated_input=updated_input,
                decision_reason={"type": "mode", "mode": mode},
            )
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            updated_input=updated_input,
            message=f"Modifying operations like '{tool.name}' require confirmation in plan mode.",
            decision_reason={"type": "mode", "mode": mode},
        )

    # 4. Passthrough -> Ask conversion (dontAsk and headless are applied by the outer gate)
    if tool_perm_res.behavior == PermissionBehavior.PASSTHROUGH:
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=tool_perm_res.message or f"Permission requested for {tool.name}.",
            decision_reason=tool_perm_res.decision_reason or {"type": "other", "reason": "passthrough"},
        )

    return tool_perm_res
