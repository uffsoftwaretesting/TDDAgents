"""
The answer an SDK host (or a permission prompt tool) gives to a permission question.

Ported from `reference/claude-code/src/utils/permissions/PermissionPromptToolResultSchema.ts` ->
`outputSchema` (`PermissionAllowResultSchema` | `PermissionDenyResultSchema`, with
`decisionClassificationField`), `inputSchema` and
`permissionPromptToolResultToPermissionDecision`.

`allow` carries `updatedInput` (an empty object means "the original input": a client answering
from a notification has no copy of it) and may carry `updatedPermissions`, applied to the run and
persisted; malformed permission updates and an unknown `decisionClassification` are dropped, never
a rejection of the whole answer (anthropics/claude-code#29440). `deny` carries `message`, and with
`interrupt` aborts the turn. The decision's reason is `permissionPromptTool`, naming the tool asked
about.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.types.permissions import PermissionBehavior, PermissionResult

logger = logging.getLogger(__name__)

#: `decisionClassificationField`'s enum.
DECISION_CLASSIFICATIONS = ("user_temporary", "user_permanent", "user_reject")


class PermissionPromptOutputError(ValueError):
    """The answer is neither an `allow` nor a `deny` (`outputSchema.parse` failed)."""


def parse_permission_prompt_output(raw: Any) -> dict[str, Any]:
    """`outputSchema().parse(raw)`: the validated answer, or `PermissionPromptOutputError`."""
    if not isinstance(raw, Mapping):
        raise PermissionPromptOutputError("Expected object")
    behavior = raw.get("behavior")
    result: dict[str, Any] = {"behavior": behavior}
    if behavior == "allow":
        updated_input = raw.get("updatedInput")
        if not isinstance(updated_input, Mapping):
            raise PermissionPromptOutputError("updatedInput: Expected object")
        result["updatedInput"] = dict(updated_input)
        if "updatedPermissions" in raw:
            result["updatedPermissions"] = _updated_permissions(raw.get("updatedPermissions"))
    elif behavior == "deny":
        message = raw.get("message")
        if not isinstance(message, str):
            raise PermissionPromptOutputError("message: Expected string")
        result["message"] = message
        interrupt = raw.get("interrupt")
        if interrupt is not None:
            if not isinstance(interrupt, bool):
                raise PermissionPromptOutputError("interrupt: Expected boolean")
            result["interrupt"] = interrupt
    else:
        raise PermissionPromptOutputError(f"Invalid behavior: {behavior!r}")
    tool_use_id = raw.get("toolUseID")
    if tool_use_id is not None:
        if not isinstance(tool_use_id, str):
            raise PermissionPromptOutputError("toolUseID: Expected string")
        result["toolUseID"] = tool_use_id
    classification = raw.get("decisionClassification")
    if classification in DECISION_CLASSIFICATIONS:
        result["decisionClassification"] = classification
    return result


def _updated_permissions(raw: Any) -> list[Any] | None:
    """`z.array(permissionUpdateSchema()).optional().catch(undefined)`."""
    from app.utils.permissions.permission_update import parse_permission_updates

    if raw is None:
        return None
    updates = parse_permission_updates(raw) if isinstance(raw, list) else None
    if updates is None or len(updates) != len(raw):
        logger.warning("Malformed updatedPermissions from SDK host ignored")
        return None
    return list(updates)


def permission_prompt_tool_result_to_permission_decision(
    result: Mapping[str, Any], tool: Any, input_args: dict[str, Any], context: Any
) -> PermissionResult:
    """`permissionPromptToolResultToPermissionDecision`."""
    from app.utils.permissions.permissions import persist_permissions

    reason = {"type": "permissionPromptTool", "permissionPromptToolName": tool.name, "toolResult": dict(result)}
    if result.get("behavior") == "allow":
        updates = result.get("updatedPermissions")
        if updates:
            persist_permissions(updates, context)
        updated = result.get("updatedInput") or {}
        return PermissionResult(behavior=PermissionBehavior.ALLOW, updated_input=dict(updated) or input_args,
                                decision_reason=reason)
    if result.get("interrupt"):
        logger.debug("SDK permission prompt deny+interrupt: tool=%s message=%s", tool.name, result.get("message"))
        context.cancel.cancel()
    return PermissionResult(behavior=PermissionBehavior.DENY, message=str(result.get("message", "")),
                            decision_reason=reason)


__all__ = [
    "DECISION_CLASSIFICATIONS",
    "PermissionPromptOutputError",
    "parse_permission_prompt_output",
    "permission_prompt_tool_result_to_permission_decision",
]
