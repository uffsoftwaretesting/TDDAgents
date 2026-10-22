"""
Hook attachments and the messages the model sees for them.

Ported from:
- `reference/claude-code/src/utils/attachments.ts` -> `HookAttachment` (the `hook_*` union),
  `AsyncHookResponseAttachment`, `createAttachmentMessage`
- `reference/claude-code/src/utils/messages.ts` -> `normalizeAttachmentForAPI` (the `hook_*`
  and `async_hook_response` cases), `wrapInSystemReminder`

Upstream keeps every attachment in the transcript and decides at request time which ones
reach the API. This loop has no separate transcript of attachments yet (Phase 6), so the
decision is made when the attachment is created: an attachment upstream sends to the model
becomes a meta user message wrapped in `<system-reminder>` (the TDD-state attachment's
shape), and one upstream hides from the model (`hook_cancelled`,
`hook_non_blocking_error`, `hook_error_during_execution`, `hook_system_message`, a
`hook_success` outside SessionStart/UserPromptSubmit or with no content) becomes no message
and is logged instead. The model's view is therefore exactly upstream's.

Every message keeps the attachment itself under `additional_kwargs["attachment"]`, which is
how the engine recognises `hook_stopped_continuation` (upstream's `query.ts` checks
`attachment.type`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from app.loop.messages import Message, wrap_in_system_reminder

logger = logging.getLogger("TDDOrchestrator.Hooks")

ATTACHMENT_KEY = "attachment"


@dataclass(frozen=True, slots=True)
class HookBlockingError:
    """`HookBlockingError`."""

    blocking_error: str
    command: str


@dataclass(frozen=True, slots=True)
class HookAttachment:
    """One `hook_*` attachment. Fields a given type does not use stay `None`."""

    type: str
    hook_name: str
    tool_use_id: str
    hook_event: str
    content: str | tuple[str, ...] | None = None
    blocking_error: HookBlockingError | None = None
    message: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    command: str | None = None
    duration_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "type": self.type,
            "hookName": self.hook_name,
            "toolUseID": self.tool_use_id,
            "hookEvent": self.hook_event,
        }
        optional: dict[str, Any] = {
            "content": list(self.content) if isinstance(self.content, tuple) else self.content,
            "message": self.message,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exitCode": self.exit_code,
            "command": self.command,
            "durationMs": self.duration_ms,
        }
        if self.blocking_error is not None:
            optional["blockingError"] = {
                "blockingError": self.blocking_error.blocking_error,
                "command": self.blocking_error.command,
            }
        data.update({k: v for k, v in optional.items() if v is not None})
        return data


def normalize_attachment_for_api(attachment: HookAttachment) -> str | None:
    """The text `normalizeAttachmentForAPI` sends for a hook attachment, or `None`."""
    kind = attachment.type
    if kind == "hook_blocking_error" and attachment.blocking_error is not None:
        return (
            f'{attachment.hook_name} hook blocking error from command: '
            f'"{attachment.blocking_error.command}": {attachment.blocking_error.blocking_error}'
        )
    if kind == "hook_success":
        if attachment.hook_event not in ("SessionStart", "UserPromptSubmit"):
            return None
        if not attachment.content:
            return None
        return f"{attachment.hook_name} hook success: {attachment.content}"
    if kind == "hook_additional_context":
        content = attachment.content
        lines = list(content) if isinstance(content, tuple) else ([content] if content else [])
        if not lines:
            return None
        return f"{attachment.hook_name} hook additional context: " + "\n".join(lines)
    if kind == "hook_stopped_continuation":
        return f"{attachment.hook_name} hook stopped continuation: {attachment.message}"
    return None


def create_attachment_message(attachment: HookAttachment) -> Message | None:
    """
    `createAttachmentMessage` + `normalizeAttachmentForAPI`: the meta message the model will
    see, or `None` (logged) for an attachment upstream keeps out of the request.
    """
    text = normalize_attachment_for_api(attachment)
    if text is None:
        logger.info("Hook attachment %s (%s): %s", attachment.type, attachment.hook_name, attachment.as_dict())
        return None
    return HumanMessage(
        content=wrap_in_system_reminder(text),
        additional_kwargs={"is_meta": True, ATTACHMENT_KEY: attachment.as_dict()},
    )


def attachment_type_of(message: Any) -> str | None:
    """The hook attachment type a message carries, if any."""
    kwargs = getattr(message, "additional_kwargs", None)
    if not isinstance(kwargs, Mapping):
        return None
    attachment = kwargs.get(ATTACHMENT_KEY)
    if not isinstance(attachment, Mapping):
        return None
    kind = attachment.get("type")
    return kind if isinstance(kind, str) else None


def is_hook_stopped_continuation(message: Any) -> bool:
    """`query.ts`: `attachment.type === 'hook_stopped_continuation'`."""
    return attachment_type_of(message) == "hook_stopped_continuation"


def async_hook_response_messages(response: Mapping[str, Any], attachment: Mapping[str, Any]) -> tuple[Message, ...]:
    """
    `normalizeAttachmentForAPI`'s `async_hook_response` case: the response's
    `systemMessage` and `additionalContext`, each as its own meta message, wrapped.
    """
    texts: list[str] = []
    system_message = response.get("systemMessage")
    if isinstance(system_message, str) and system_message:
        texts.append(system_message)
    specific = response.get("hookSpecificOutput")
    if isinstance(specific, Mapping):
        context = specific.get("additionalContext")
        if isinstance(context, str) and context:
            texts.append(context)
    return tuple(
        HumanMessage(
            content=wrap_in_system_reminder(text),
            additional_kwargs={"is_meta": True, ATTACHMENT_KEY: dict(attachment)},
        )
        for text in texts
    )


def is_attachment_message(message: Any) -> bool:
    """A meta message created from an attachment (hook or async-hook response)."""
    kwargs = getattr(message, "additional_kwargs", None)
    return isinstance(kwargs, Mapping) and isinstance(kwargs.get(ATTACHMENT_KEY), Mapping)


def reorder_attachments_for_api(messages: Sequence[Message]) -> list[Message]:
    """
    `reorderAttachmentsForAPI`, for this message format: attachments emitted between the
    results of one batch of tool calls move after the last of those results.

    Upstream bubbles attachments up to the nearest assistant message or tool result and lets
    its user-message merge put them beside the results. Here a tool result is its own
    `ToolMessage`, and a provider rejects anything between an assistant's tool calls and
    their results, so the attachments go after the whole contiguous block instead. The order
    among attachments, and among results, is kept.
    """
    out: list[Message] = []
    i = 0
    items = list(messages)
    while i < len(items):
        message = items[i]
        if getattr(message, "type", None) != "tool":
            out.append(message)
            i += 1
            continue
        results: list[Message] = []
        held: list[Message] = []
        while i < len(items) and (
            getattr(items[i], "type", None) == "tool" or is_attachment_message(items[i])
        ):
            (results if getattr(items[i], "type", None) == "tool" else held).append(items[i])
            i += 1
        out.extend(results)
        out.extend(held)
    return out


def messages_for(attachments: Sequence[HookAttachment]) -> tuple[Message, ...]:
    """Every model-visible message among `attachments`, in order."""
    out: list[Message] = []
    for attachment in attachments:
        message = create_attachment_message(attachment)
        if message is not None:
            out.append(message)
    return tuple(out)


__all__ = [
    "ATTACHMENT_KEY",
    "HookAttachment",
    "HookBlockingError",
    "async_hook_response_messages",
    "attachment_type_of",
    "create_attachment_message",
    "is_attachment_message",
    "is_hook_stopped_continuation",
    "messages_for",
    "normalize_attachment_for_api",
    "reorder_attachments_for_api",
]
