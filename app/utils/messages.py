"""
The loop's message currency, declared in exactly one place.

`Message` is an alias rather than a class because the loop does not own the message
format — the model seam does. Today every agent module and the chat-model factory
speak `langchain_core.messages.BaseMessage`, so adopting a second
vocabulary here would buy nothing and cost an adapter at every boundary.

The alias exists so that decision stays revisable: if Part F1 moves the model call onto a
provider SDK, this line is the whole migration surface, and every signature that mentions
`Message` follows it. Import `Message` from here rather than importing `BaseMessage`
directly, or that property is lost the first time someone shortcuts it.

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

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCall

Message = BaseMessage


def tool_calls_in(message: Message) -> tuple[ToolCall, ...]:
    """
    The tool calls a message is asking for, or nothing.

    Upstream reads this by filtering an assistant message's content blocks for
    `type === 'tool_use'` (`reference/claude-code/src/query.ts`, where a non-empty result
    sets `needsFollowUp`). The LangChain equivalent is `AIMessage.tool_calls`, which is
    already the parsed form.

    Read through `getattr` rather than an `isinstance` check because the decision the loop
    makes is "did this message ask for anything", and a message type that grows tool calls
    later — or a provider adapter that is not an `AIMessage` — should answer it the same
    way. A message without the attribute, or with it set to nothing, simply is not asking:
    both must answer "no calls" rather than raise, because this runs against every message
    a model streams back and one of them raising would abort the turn.
    """
    calls: list[ToolCall] | None = getattr(message, "tool_calls", None)
    return tuple(calls or ())


def wrap_in_system_reminder(content: str) -> str:
    """
    `src/utils/messages.ts` -> `wrapInSystemReminder`. Lives here, in the leaf module, so
    the hook system can wrap its attachments without importing the context package.
    """
    return f"<system-reminder>\n{content}\n</system-reminder>"


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


def _as_blocks(content: Any) -> list[Any]:
    if isinstance(content, list):
        return list(content)
    return [{"type": "text", "text": content}] if content else []


def _merged_content(a: Any, b: Any) -> Any:
    if isinstance(a, str) and isinstance(b, str):
        return a + b
    return [*_as_blocks(a), *_as_blocks(b)]


def _same_response_index(out: Sequence[Message], response_id: str) -> int | None:
    """The earlier block of the same response, walking back over tool results only."""
    for index in range(len(out) - 1, -1, -1):
        previous = out[index]
        if isinstance(previous, AIMessage):
            if previous.id == response_id:
                return index
            continue
        if not isinstance(previous, ToolMessage):
            return None
    return None


def merge_split_assistant_messages(messages: Sequence[Message]) -> list[Message]:
    """
    `normalizeMessagesForAPI`'s assistant merge (`mergeAssistantMessages`): the content blocks of
    one model response, stored as one message each (`app/services/api/claude.py`), are merged
    back into one assistant message by their shared response id before sending. As upstream it
    walks back over tool results, so a response's parallel tool calls become one assistant
    message followed by their results, as a provider requires. The stored history is untouched.
    """
    out: list[Message] = []
    for message in messages:
        index = _same_response_index(out, message.id) if isinstance(message, AIMessage) and message.id else None
        if index is None:
            out.append(message)
            continue
        previous = out[index]
        assert isinstance(previous, AIMessage) and isinstance(message, AIMessage)
        out[index] = previous.model_copy(update={
            "content": _merged_content(previous.content, message.content),
            "tool_calls": [*previous.tool_calls, *message.tool_calls],
        })
    return out


#: What a tool result whose images moved says in their place (`move_tool_result_media_to_user`).
TOOL_RESULT_MEDIA_MOVED = "[The image from this tool result follows in the next user message.]"


def _is_media_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in ("image", "image_url")


def move_tool_result_media_to_user(messages: Sequence[Message]) -> list[Message]:
    """
    For a provider whose tool messages cannot carry images (OpenAI): each tool result's image
    blocks move into one meta user message after that batch of results, and the result keeps
    its text, or a note saying where the image went. Anthropic accepts images in a tool result
    (upstream's `FileReadTool.mapToolResultToToolResultBlockParam`), so its requests are
    untouched. The stored history is untouched either way.
    """
    out: list[Message] = []
    pending: list[Any] = []
    for message in messages:
        if isinstance(message, ToolMessage) and isinstance(message.content, list) and any(
                _is_media_block(b) for b in message.content):
            media = [b for b in message.content if _is_media_block(b)]
            text = [b for b in message.content if not _is_media_block(b)]
            pending.extend(media)
            out.append(message.model_copy(update={"content": text or TOOL_RESULT_MEDIA_MOVED}))
            continue
        if pending and not isinstance(message, ToolMessage):
            out.append(HumanMessage(content=pending, additional_kwargs={"is_meta": True}))
            pending = []
        out.append(message)
    if pending:
        out.append(HumanMessage(content=pending, additional_kwargs={"is_meta": True}))
    return out


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
    "Message",
    "ToolCall",
    "async_hook_response_messages",
    "attachment_type_of",
    "create_attachment_message",
    "is_attachment_message",
    "is_hook_stopped_continuation",
    "messages_for",
    "normalize_attachment_for_api",
    "reorder_attachments_for_api",
    "tool_calls_in",
    "wrap_in_system_reminder",
]


#: `REJECT_MESSAGE` (`src/utils/messages.ts`): the user said no at a permission prompt.
REJECT_MESSAGE = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file edit, "
    "the new_string was NOT written to the file). STOP what you are doing and wait for the user to tell you how "
    "to proceed."
)
#: `REJECT_MESSAGE_WITH_REASON_PREFIX`: the same, followed by what the user said instead.
REJECT_MESSAGE_WITH_REASON_PREFIX = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file edit, "
    "the new_string was NOT written to the file). To tell you how to proceed, the user said:\n"
)

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


def dont_ask_reject_message(tool_name: str) -> str:
    """`DONT_ASK_REJECT_MESSAGE`."""
    return (
        f"Permission to use {tool_name} has been denied because the agent is running in don't ask mode. "
        f"{DENIAL_WORKAROUND_GUIDANCE}"
    )
