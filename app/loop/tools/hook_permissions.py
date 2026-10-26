"""
Resolving a PreToolUse hook's verdict against the permission rules.

Ported from `reference/claude-code/src/services/tools/toolHooks.ts` ->
`resolveHookPermissionDecision`, whose own docstring states the invariant it exists for:

    Encapsulates the invariant that hook 'allow' does NOT bypass settings.json
    deny/ask rules — checkRuleBasedPermissions still applies (inc-4788 analog).

* **allow** — re-checked with `check_rule_based_permissions` only (the TDD managed tier,
  deny and ask rules, the tool's deny, content ask rules, safety checks). No objection: the
  hook's allow stands, so it *does* skip an 'ask' no rule produced (a passthrough, a
  default-mode write) — in headless and dontAsk runs too, exactly as upstream. A rule deny
  wins; a rule ask goes to the full gate. A tool that requires interaction goes to the full
  gate unless the hook supplied `updatedInput` (the hook *was* the interaction).
* **deny** — final, on the original input.
* **ask** — upstream's `forceDecision`, which skips `hasPermissionsToUseTool` entirely.
  *Divergence (TDD, and v2.1.101's "ask no longer overrides deny" fix):* the TDD tier and
  deny rules run first; only then is the hook's ask handled as an ask — denied under
  dontAsk, offered to PermissionRequest hooks when no prompt can be shown, else returned.
* **no verdict** — the full gate.

The verdict comes from `app/loop/hooks/tool_hooks.py::run_pre_tool_use_hooks`.

Not ported: upstream's `requireCanUseTool` context flag (speculative overlays), which this
loop has no use for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.loop.permissions.gate import (
    has_permissions_to_use_tool,
    resolve_ask_without_prompt,
)
from app.loop.permissions.messages import create_permission_request_message
from app.loop.permissions.rules import (
    check_rule_based_permissions,
    requires_user_interaction,
)
from app.loop.permissions.types import PermissionBehavior, PermissionResult

if TYPE_CHECKING:
    from app.loop.context.tool_context import ToolContext
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
    tool_use_id: str = "",
) -> ResolvedPermission:
    """`resolveHookPermissionDecision`: the decision and the input the tool will run on."""
    behavior = hook_verdict.behavior if hook_verdict is not None else None
    hook_name = f"PreToolUse:{tool.name}"

    if hook_verdict is not None and behavior == PermissionBehavior.ALLOW:
        hook_input = hook_verdict.updated_input if hook_verdict.updated_input is not None else input_args
        interaction_satisfied = hook_verdict.updated_input is not None
        if requires_user_interaction(tool, hook_input) and not interaction_satisfied:
            decision = await has_permissions_to_use_tool(tool, hook_input, context, tool_use_id)
            return ResolvedPermission(decision=decision, input=hook_input)
        rule_check = await check_rule_based_permissions(tool, hook_input, context)
        if rule_check is None:
            # No rule objects: the hook's approval stands and the prompt is skipped.
            return ResolvedPermission(
                decision=PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    updated_input=hook_input,
                    decision_reason={"type": "hook", "hookName": hook_name},
                ),
                input=hook_input,
            )
        if rule_check.behavior == PermissionBehavior.DENY:
            return ResolvedPermission(decision=rule_check, input=hook_input)
        # An ask rule: the prompt is required despite the hook's approval.
        decision = await has_permissions_to_use_tool(tool, hook_input, context, tool_use_id)
        return ResolvedPermission(decision=decision, input=hook_input)

    if hook_verdict is not None and behavior == PermissionBehavior.DENY:
        return ResolvedPermission(
            decision=PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=hook_verdict.message or f"Permission to use {tool.name} has been denied.",
                decision_reason={"type": "hook", "hookName": hook_name},
            ),
            input=input_args,
        )

    if hook_verdict is not None and behavior == PermissionBehavior.ASK:
        ask_input = hook_verdict.updated_input if hook_verdict.updated_input is not None else input_args
        rule_check = await check_rule_based_permissions(tool, ask_input, context)
        if rule_check is not None and rule_check.behavior == PermissionBehavior.DENY:
            return ResolvedPermission(decision=rule_check, input=ask_input)
        forced = PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=hook_verdict.message or create_permission_request_message(
                tool.name, {"type": "hook", "hookName": hook_name}),
            decision_reason={"type": "hook", "hookName": hook_name, "reason": hook_verdict.message},
        )
        decision = await resolve_ask_without_prompt(forced, tool, ask_input, context, tool_use_id)
        return ResolvedPermission(decision=decision, input=ask_input)

    return ResolvedPermission(
        decision=await has_permissions_to_use_tool(tool, input_args, context, tool_use_id),
        input=input_args,
    )


__all__ = [
    "HookPermissionVerdict",
    "ResolvedPermission",
    "resolve_hook_permission_decision",
]
