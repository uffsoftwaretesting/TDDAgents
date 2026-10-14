"""
PermissionRequest hooks for a context that cannot show a prompt.

Ported from `reference/claude-code/src/utils/permissions/permissions.ts` ->
`runPermissionRequestHooksForHeadlessAgent`, and the decision shape of
`src/utils/hooks.ts` -> `processHookJSONOutput` (`case 'PermissionRequest'`).

Upstream runs these hooks only when the gate's answer is still 'ask' and no one can be
asked. The first hook that states a decision wins:

* `allow` — the call runs, on the hook's `updatedInput` if it gave one;
* `deny` — refused with the hook's message, and `interrupt: true` aborts the turn;
* no decision, or a hook that fails — fall through to the headless auto-deny.

**This cannot reopen a TDD phase.** A phase violation is a `deny` from the gate's managed
tier, and a deny never becomes an 'ask', so no PermissionRequest hook is ever consulted
about it. What a hook *can* do is approve a call no rule covers — an operator decision, made
in configuration the agent cannot write, exactly as upstream intends.

The hook runner is the run's `ToolContext.hook_dispatcher` (today `app/hooks/`, rebuilt
against the loop in Phase 4). Divergence: `updatedPermissions` are not persisted or applied
— there is no settings-writing path in this loop yet — and are logged instead.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Mapping

from app.loop.permissions.types import PermissionBehavior, PermissionMode, PermissionResult

if TYPE_CHECKING:
    from app.loop.context import ToolContext
    from app.loop.tools.base import Tool

logger = logging.getLogger("TDDOrchestrator.Permissions")

PERMISSION_REQUEST_EVENT = "PermissionRequest"


def permission_request_decision(outcome: Any) -> Mapping[str, Any] | None:
    """The hook decision carried by a dispatcher outcome, if it carries a valid one."""
    decision = getattr(outcome, "permission_request_result", None)
    if isinstance(decision, Mapping) and decision.get("behavior") in ("allow", "deny"):
        return decision
    return None


async def run_permission_request_hooks_for_headless_agent(
    tool: Tool,
    input_args: dict[str, Any],
    tool_use_id: str,
    context: ToolContext,
    permission_mode: PermissionMode | str,
) -> PermissionResult | None:
    """`runPermissionRequestHooksForHeadlessAgent`: a hook's decision, or `None`."""
    dispatcher = getattr(context, "hook_dispatcher", None)
    if dispatcher is None:
        return None
    try:
        outcome = dispatcher.run(
            PERMISSION_REQUEST_EVENT,
            tool_name=tool.name,
            tool_input=input_args,
            ctx_fields={"permission_mode": str(permission_mode), "tool_use_id": tool_use_id},
        )
    except Exception:
        # If hooks fail, fall through to the auto-deny rather than crashing.
        logger.exception("PermissionRequest hook failed for headless agent")
        return None
    decision = permission_request_decision(outcome)
    if decision is None:
        return None
    if decision["behavior"] == "allow":
        if decision.get("updatedPermissions"):
            logger.warning("Ignoring updatedPermissions from a PermissionRequest hook: nothing persists them here.")
        updated = decision.get("updatedInput")
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=dict(updated) if isinstance(updated, Mapping) else input_args,
            decision_reason={"type": "hook", "hookName": PERMISSION_REQUEST_EVENT},
        )
    message = decision.get("message")
    if decision.get("interrupt") is True:
        logger.info("Hook interrupt: tool=%s hookMessage=%s", tool.name, message)
        context.cancel.cancel()
    return PermissionResult(
        behavior=PermissionBehavior.DENY,
        message=str(message) if message else "Permission denied by hook",
        decision_reason={"type": "hook", "hookName": PERMISSION_REQUEST_EVENT, "reason": message},
    )


__all__ = [
    "PERMISSION_REQUEST_EVENT",
    "permission_request_decision",
    "run_permission_request_hooks_for_headless_agent",
]
