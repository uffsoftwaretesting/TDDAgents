"""
`canUseTool` for an interactive session: a permission 'ask' becomes a question to the user
(Phase 11).

Ported from `reference/claude-code/src/hooks/useCanUseTool.tsx`,
`hooks/toolPermission/handlers/interactiveHandler.ts` (`handleInteractivePermission`) and
`hooks/toolPermission/PermissionContext.ts` (`handleUserAllow`, `cancelAndAbort`,
`persistPermissions`). The gate (`has_permissions_to_use_tool`) has already decided by the
time this runs, so the TDD phase tier — which denies, never asks — is out of its reach: no
answer here can open a phase-forbidden write.

1. PermissionRequest hooks have the first word, as upstream's race against the dialog does.
2. Otherwise the user is asked, with the tool's options (`components/permissions/`).
3. "Yes" allows; a "don't ask again" / "during this session" choice also persists its
   permission updates and applies them to the run (re-stripped of dangerous allows).
4. "No" rejects with upstream's `REJECT_MESSAGE`; without feedback the turn is aborted, so
   control returns to the user; with feedback the model hears it and goes on.

Divergences: the hooks run before the question, not in a race with it; an accept-with-feedback
input, the classifier pre-check and the bridge/channel relays are not ported.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.components.permissions.permission_options import (
    PermissionOption,
    permission_options,
)
from app.types.permissions import PermissionBehavior, PermissionResult
from app.utils.messages import REJECT_MESSAGE, REJECT_MESSAGE_WITH_REASON_PREFIX
from app.utils.permissions.filesystem import permission_cwd
from app.utils.permissions.permissions import (
    extract_permission_context,
    persist_permissions,
    run_permission_request_hooks_for_headless_agent,
)


@dataclass(frozen=True, slots=True)
class PermissionPrompt:
    """What the user is asked: which tool, doing what, why, and the choices."""

    tool_name: str
    description: str
    message: str
    input: dict[str, Any]
    options: tuple[PermissionOption, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PermissionAnswer:
    """The user's choice (an option's `value`) and what they said with it."""

    value: str
    feedback: str | None = None


AskUser = Callable[[PermissionPrompt], Awaitable[PermissionAnswer]]
#: `CanUseToolFn`: the gate's 'ask', resolved — (tool, input, ask, context, tool_use_id) -> decision.
CanUseTool = Callable[[Any, dict[str, Any], PermissionResult, Any, str], Coroutine[Any, Any, PermissionResult]]


def rejection(feedback: str | None) -> PermissionResult:
    """`cancelAndAbort`'s message: upstream answers with an 'ask' carrying the rejection."""
    message = f"{REJECT_MESSAGE_WITH_REASON_PREFIX}{feedback}" if feedback else REJECT_MESSAGE
    return PermissionResult(behavior=PermissionBehavior.ASK, message=message)


def _chosen(options: Sequence[PermissionOption], value: str) -> PermissionOption:
    """The option the answer names; an answer naming none is a "No"."""
    return next((o for o in options if o.value == value), PermissionOption(value, value, reject=True))


def build_interactive_can_use_tool(ask_user: AskUser) -> CanUseTool:
    """The REPL's `canUseTool`, asking through `ask_user`."""

    async def can_use_tool(
        tool: Any, input_args: dict[str, Any], result: PermissionResult, context: Any, tool_use_id: str
    ) -> PermissionResult:
        if result.behavior != PermissionBehavior.ASK:
            return result
        perm = extract_permission_context(context)
        hooked = await run_permission_request_hooks_for_headless_agent(
            tool, input_args, tool_use_id, context, perm.mode
        )
        if hooked is not None:
            return hooked
        options = permission_options(tool.name, input_args, result.suggestions, perm, permission_cwd(context))
        answer = await ask_user(PermissionPrompt(
            tool_name=tool.name,
            description=tool.description(input_args),
            message=result.message,
            input=input_args,
            options=tuple(options),
        ))
        choice = _chosen(options, answer.value)
        if choice.reject:
            feedback = (answer.feedback or "").strip() or None
            if feedback is None:
                # `cancelAndAbort`: a plain "No" stops the turn and hands control back.
                context.cancel.cancel()
            return rejection(feedback)
        persist_permissions(choice.updates, context)
        return PermissionResult(behavior=PermissionBehavior.ALLOW, updated_input=input_args,
                                decision_reason={"type": "user", "option": choice.value})

    return can_use_tool
