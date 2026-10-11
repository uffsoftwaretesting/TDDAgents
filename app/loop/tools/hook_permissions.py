"""
Resolving a PreToolUse hook's verdict against the permission rules (Phase 2).

Ported from `reference/claude-code/src/services/tools/toolHooks.ts` ->
`resolveHookPermissionDecision`, whose own docstring states the invariant it exists for:

    Encapsulates the invariant that hook 'allow' does NOT bypass settings.json
    deny/ask rules — checkRuleBasedPermissions still applies (inc-4788 analog).

So a hook's `allow` buys exactly one thing: skipping the interactive prompt. It is still
re-checked, and a deny rule overrides it. That matters more here than upstream, because the
TDD phase rules are evaluated inside the same gate: a PreToolUse hook that approved a write
must not be able to put production code on disk during RED.

A hook `deny` is final and is returned unchanged — upstream does not even look at its
`updatedInput`, "since tool won't run".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from app.loop.permissions.gate import has_permissions_to_use_tool
from app.loop.permissions.types import PermissionBehavior, PermissionResult

if TYPE_CHECKING:
    from app.loop.context import ToolContext
    from app.loop.tools.base import Tool


@dataclass(frozen=True, slots=True)
class HookPermissionVerdict:
    """A PreToolUse hook's permission opinion, as the dispatcher reports it."""

    behavior: PermissionBehavior | None = None
    message: str = ""
    updated_input: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPermission:
    """`resolveHookPermissionDecision`'s `{decision, input}`."""

    decision: PermissionResult
    input: dict[str, Any]


async def resolve_hook_permission_decision(
    hook_verdict: HookPermissionVerdict | None,
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
) -> ResolvedPermission:
    """
    Combine a hook's verdict with the rule-based gate, in upstream's order.

    * `deny` — returned immediately, on the unchanged input.
    * `allow` — the gate still runs. A gate `deny` wins; a gate `ask` still asks; only a
      gate that raises no objection lets the hook's allow stand.
    * `ask` or no verdict — the ordinary gate decides, and a hook `ask` rides along as the
      message so the reason reaching the model is the hook's own.
    """
    if hook_verdict is None or hook_verdict.behavior is None:
        return ResolvedPermission(
            decision=await has_permissions_to_use_tool(tool, input_args, context),
            input=input_args,
        )

    if hook_verdict.behavior == PermissionBehavior.DENY:
        return ResolvedPermission(
            decision=PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=hook_verdict.message or f"Permission to use {tool.name} has been denied.",
                decision_reason={"type": "hook", "hookName": f"PreToolUse:{tool.name}"},
            ),
            input=input_args,
        )

    hook_input = hook_verdict.updated_input if hook_verdict.updated_input is not None else input_args

    if hook_verdict.behavior == PermissionBehavior.ALLOW:
        rule_check = await has_permissions_to_use_tool(tool, hook_input, context)
        if rule_check.behavior == PermissionBehavior.ALLOW:
            # No objection: the hook's approval stands and the prompt is skipped.
            return ResolvedPermission(
                decision=PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    updated_input=hook_input,
                    decision_reason={"type": "hook", "hookName": f"PreToolUse:{tool.name}"},
                ),
                input=hook_input,
            )
        # A deny or ask from the gate overrides the hook, deliberately.
        return ResolvedPermission(decision=rule_check, input=hook_input)

    # `ask`: the gate decides, carrying the hook's message as the reason.
    decision = await has_permissions_to_use_tool(tool, hook_input, context)
    if decision.behavior == PermissionBehavior.ASK and hook_verdict.message:
        decision = PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=hook_verdict.message,
            updated_input=decision.updated_input,
            decision_reason={"type": "hook", "hookName": f"PreToolUse:{tool.name}"},
        )
    return ResolvedPermission(decision=decision, input=hook_input)


def verdict_from_hook_outcome(outcome: Any) -> HookPermissionVerdict | None:
    """
    Read a dispatcher outcome as a permission verdict.

    `app/hooks/` reports a veto as `denied`; a richer JSON verdict arrives as
    `permission_behavior`. Anything else is not an opinion about permission, and returning
    `None` keeps the ordinary gate in charge.
    """
    if outcome is None:
        return None
    behavior: PermissionBehavior | None = None
    raw = getattr(outcome, "permission_behavior", None)
    if isinstance(raw, PermissionBehavior):
        behavior = raw
    elif isinstance(raw, str):
        try:
            behavior = PermissionBehavior(raw)
        except ValueError:
            behavior = None
    if behavior is None and getattr(outcome, "denied", False):
        behavior = PermissionBehavior.DENY
    if behavior is None:
        return None
    updated = getattr(outcome, "updated_input", None)
    return HookPermissionVerdict(
        behavior=behavior,
        message=str(getattr(outcome, "reason", "") or ""),
        updated_input=dict(updated) if isinstance(updated, Mapping) else None,
    )


__all__ = [
    "HookPermissionVerdict",
    "ResolvedPermission",
    "resolve_hook_permission_decision",
    "verdict_from_hook_outcome",
]
