"""
The hooks around a run's input: SessionStart before it, UserPromptSubmit on its prompt.

Ported from:
- `reference/claude-code/src/utils/sessionStart.ts` -> `processSessionStartHooks`
- `reference/claude-code/src/utils/processUserInput/processUserInput.ts` -> the
  UserPromptSubmit block of `processUserInput`
- `reference/claude-code/src/utils/hooks.ts` -> `getUserPromptSubmitHookBlockingMessage`

One loop run is one headless session (upstream's `-p` invocation), so a run fires
SessionStart with `source: "startup"` and then UserPromptSubmit with its sub-requirement as
the prompt, before the first model call.

* **SessionStart** — hook output reaches the model as messages placed before the prompt:
  a plain-text hook's stdout (`hook_success`) and every `additionalContext`, joined into one
  `hook_additional_context`. An `initialUserMessage` is returned for the runner.
* **UserPromptSubmit** — a blocking error erases the prompt: the run does not query the
  model. `continue: false` keeps the prompt but also stops before the model. Otherwise the
  hooks' context follows the prompt as messages.

Not ported: plugin-hook loading (no plugins), `watchPaths` / FileChanged watching, and the
10 000-character `applyTruncation` of hook output (no UI to protect).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.types.hooks import get_user_prompt_submit_hook_blocking_message
from app.utils.hooks.hooks import (
    HookDispatcher,
    execute_session_start_hooks,
    execute_user_prompt_submit_hooks,
)
from app.utils.messages import HookAttachment, Message, create_attachment_message


@dataclass
class SessionStartOutcome:
    """`processSessionStartHooks`' messages, plus the pending `initialUserMessage`."""

    messages: list[Message] = field(default_factory=list)
    initial_user_message: str | None = None


async def process_session_start_hooks(
    dispatcher: HookDispatcher | None,
    source: str,
    agent_type: str | None = None,
    model: str | None = None,
) -> SessionStartOutcome:
    """`processSessionStartHooks`."""
    outcome = SessionStartOutcome()
    contexts: list[str] = []
    async for result in execute_session_start_hooks(dispatcher, source, agent_type, model):
        if result.attachment is not None:
            message = create_attachment_message(result.attachment)
            if message is not None:
                outcome.messages.append(message)
        if result.additional_contexts:
            contexts.extend(result.additional_contexts)
        if result.initial_user_message:
            outcome.initial_user_message = result.initial_user_message
    if contexts:
        message = create_attachment_message(
            HookAttachment(
                type="hook_additional_context",
                content=tuple(contexts),
                hook_name="SessionStart",
                tool_use_id="SessionStart",
                hook_event="SessionStart",
            )
        )
        if message is not None:
            outcome.messages.append(message)
    return outcome


@dataclass
class UserPromptSubmitOutcome:
    """What `processUserInput` returns after the UserPromptSubmit hooks."""

    messages: list[Message] = field(default_factory=list)
    should_query: bool = True
    blocked_message: str | None = None
    stopped_message: str | None = None


async def process_user_prompt_submit_hooks(
    context: object, prompt: str, permission_mode: str
) -> UserPromptSubmitOutcome:
    """The UserPromptSubmit part of `processUserInput`, consumed in order."""
    outcome = UserPromptSubmitOutcome()
    async for result in execute_user_prompt_submit_hooks(context, prompt, permission_mode):
        if result.blocking_error is not None:
            blocking = get_user_prompt_submit_hook_blocking_message(result.blocking_error)
            return UserPromptSubmitOutcome(
                should_query=False, blocked_message=f"{blocking}\n\nOriginal prompt: {prompt}"
            )
        if result.prevent_continuation:
            outcome.stopped_message = "Operation stopped by hook" + (
                f": {result.stop_reason}" if result.stop_reason else ""
            )
            outcome.should_query = False
            return outcome
        if result.additional_contexts:
            message = create_attachment_message(
                HookAttachment(
                    type="hook_additional_context",
                    content=tuple(result.additional_contexts),
                    hook_name="UserPromptSubmit",
                    tool_use_id="hook-UserPromptSubmit",
                    hook_event="UserPromptSubmit",
                )
            )
            if message is not None:
                outcome.messages.append(message)
        if result.attachment is not None:
            message = create_attachment_message(result.attachment)
            if message is not None:
                outcome.messages.append(message)
    return outcome


__all__ = [
    "SessionStartOutcome",
    "UserPromptSubmitOutcome",
    "process_session_start_hooks",
    "process_user_prompt_submit_hooks",
]
