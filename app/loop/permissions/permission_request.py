"""
PermissionRequest hooks for a context that cannot show a prompt.

Ported from `reference/claude-code/src/utils/permissions/permissions.ts` ->
`runPermissionRequestHooksForHeadlessAgent`, and the decision shape of
`src/types/hooks.ts` (`PermissionRequest` `decision`) / `src/utils/hooks.ts` ->
`processHookJSONOutput`.

Upstream runs these hooks only when the gate's answer is still 'ask' and no one can be
asked. The first hook that states a decision wins:

* `allow` — the call runs, on the hook's `updatedInput` if it gave one, and the hook's
  `updatedPermissions` are persisted (`persistPermissionUpdates`) and applied to the run's
  context (`applyPermissionUpdates`);
* `deny` — refused with the hook's message, and `interrupt: true` aborts the turn;
* no decision, an invalid one, or a hook that fails — fall through to the headless deny.

A hook allow approves a call no rule covers, and overrides an ask rule or a safety check:
that is upstream's v2.1.88 behaviour, kept. Two TDD guards are added, each recorded as a
divergence:

* **A rewritten input is re-checked.** The phase tier ran on the input the gate saw; a hook
  `updatedInput` could swap `tests/test_x.py` for `calc.py` after that. The TDD tier and the
  deny rules (`check_rule_based_permissions`, deny results only — the re-check v2.1.110
  later added upstream) run on it, and a deny there refuses the call without applying the
  hook's updates.
* **Applied updates are re-stripped.** `strip_dangerous_permissions` runs on the context the
  updates produce, so a hook cannot re-open the interpreter loophole with
  `addRules: Bash(python:*)`. `setMode` is applied unguarded, as v2.1.88 does; the phase
  tier sits above `bypassPermissions` regardless.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.loop.permissions.dangerous import strip_dangerous_permissions
from app.loop.permissions.rules import (
    check_rule_based_permissions,
    extract_permission_context,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
)
from app.loop.permissions.updates import (
    apply_permission_updates,
    parse_permission_updates,
    persist_permission_updates,
    settings_locations_for,
)

if TYPE_CHECKING:
    from app.loop.context import ToolContext
    from app.loop.tools.base import Tool

logger = logging.getLogger("TDDOrchestrator.Permissions")

PERMISSION_REQUEST_EVENT = "PermissionRequest"


def permission_request_decision(outcome: Any) -> Mapping[str, Any] | None:
    """
    The hook decision carried by a dispatcher outcome, if it is a valid one. An allow whose
    `updatedPermissions` fail the schema is no decision at all, as upstream's Zod parse
    rejects the whole hook output.
    """
    decision = getattr(outcome, "permission_request_result", None)
    if not isinstance(decision, Mapping) or decision.get("behavior") not in ("allow", "deny"):
        return None
    updates = decision.get("updatedPermissions")
    if updates is not None and parse_permission_updates(updates) is None:
        logger.warning("Ignoring a PermissionRequest decision with malformed updatedPermissions.")
        return None
    return decision


def apply_hook_permission_updates(updates: Any, context: ToolContext) -> None:
    """Persist, then apply and re-strip, a hook's `updatedPermissions`."""
    parsed = parse_permission_updates(updates) or ()
    if not parsed:
        return
    current = extract_permission_context(context)
    locations = settings_locations_for(current)
    if locations is not None:
        persist_permission_updates(parsed, locations)

    def apply(state: Any) -> Any:
        updated = strip_dangerous_permissions(apply_permission_updates(state.tool_permission_context, parsed))
        return replace(state, tool_permission_context=updated)

    context.set_app_state(apply)


async def run_permission_request_hooks_for_headless_agent(
    tool: Tool,
    input_args: dict[str, Any],
    tool_use_id: str,
    context: ToolContext,
    permission_mode: PermissionMode | str,
) -> PermissionResult | None:
    """`runPermissionRequestHooksForHeadlessAgent`: the first hook decision, or `None`."""
    from app.loop.hooks.execute import execute_permission_request_hooks

    try:
        async for result in execute_permission_request_hooks(
            context, tool.name, tool_use_id, input_args, str(permission_mode), None,
            getattr(context, "cancel", None),
        ):
            decision = permission_request_decision(result)
            if decision is None:
                continue
            return await _apply_decision(decision, tool, input_args, context)
    except Exception:
        # If hooks fail, fall through to the auto-deny rather than crashing.
        logger.exception("PermissionRequest hook failed for headless agent")
    return None


async def _apply_decision(
    decision: Mapping[str, Any], tool: Tool, input_args: dict[str, Any], context: ToolContext
) -> PermissionResult:
    if decision["behavior"] == "allow":
        updated = decision.get("updatedInput")
        final_input = dict(updated) if isinstance(updated, Mapping) else input_args
        if isinstance(updated, Mapping):
            recheck = await check_rule_based_permissions(tool, final_input, context)
            if recheck is not None and recheck.behavior == PermissionBehavior.DENY:
                return recheck
        apply_hook_permission_updates(decision.get("updatedPermissions"), context)
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=final_input,
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
    "apply_hook_permission_updates",
    "permission_request_decision",
    "run_permission_request_hooks_for_headless_agent",
]
