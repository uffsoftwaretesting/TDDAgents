"""
Which user messages a rewind can go back to.

Ported from `reference/claude-code/src/components/MessageSelector.tsx` ->
`selectableUserMessagesFilter`, with `utils/messages.ts` -> `isSyntheticMessage` /
`SYNTHETIC_MESSAGES` and the tags of `constants/xml.ts` it checks. File history snapshots are
made for exactly these messages (`QueryEngine.ts`, `handlePromptSubmit.ts`).

Not ported: the `MessageSelector` dialog itself (the `/rewind` picker, React/Ink UI).

Mapping onto LangChain messages: upstream's `user` type is a `HumanMessage` here; a tool result
is a `ToolMessage` (never selectable), and a `HumanMessage` carrying an attachment record is
upstream's `attachment` type.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from app.services.tools.tool_execution import CANCEL_MESSAGE
from app.utils.attachments import attachment_of
from app.utils.conversation_recovery import NO_RESPONSE_REQUESTED
from app.utils.messages import (
    INTERRUPT_MESSAGE,
    INTERRUPT_MESSAGE_FOR_TOOL_USE,
    REJECT_MESSAGE,
)

#: `constants/xml.ts`.
BASH_STDOUT_TAG = "bash-stdout"
BASH_STDERR_TAG = "bash-stderr"
LOCAL_COMMAND_STDOUT_TAG = "local-command-stdout"
LOCAL_COMMAND_STDERR_TAG = "local-command-stderr"
TICK_TAG = "tick"
TASK_NOTIFICATION_TAG = "task-notification"
TEAMMATE_MESSAGE_TAG = "teammate-message"

#: `messages.ts` -> `SYNTHETIC_MESSAGES`.
SYNTHETIC_MESSAGES = frozenset({
    INTERRUPT_MESSAGE, INTERRUPT_MESSAGE_FOR_TOOL_USE, CANCEL_MESSAGE, REJECT_MESSAGE, NO_RESPONSE_REQUESTED,
})

#: The markers of a message the user did not write (`selectableUserMessagesFilter`); the
#: teammate tag is matched without its `>`, as upstream (it carries attributes).
_NOT_USER_AUTHORED = (
    f"<{LOCAL_COMMAND_STDOUT_TAG}>", f"<{LOCAL_COMMAND_STDERR_TAG}>", f"<{BASH_STDOUT_TAG}>",
    f"<{BASH_STDERR_TAG}>", f"<{TASK_NOTIFICATION_TAG}>", f"<{TICK_TAG}>", f"<{TEAMMATE_MESSAGE_TAG}",
)


def _flag(message: Any, key: str) -> bool:
    return message.additional_kwargs.get(key) is True


def is_synthetic_message(message: Any) -> bool:
    """`isSyntheticMessage`: content whose first block is the text of a `SYNTHETIC_MESSAGES` entry."""
    content = getattr(message, "content", None)
    if not isinstance(content, list) or not content:
        return False
    first = content[0]
    return isinstance(first, dict) and first.get("type") == "text" and first.get("text") in SYNTHETIC_MESSAGES


def selectable_user_messages_filter(message: Any) -> bool:
    """`selectableUserMessagesFilter`: a prompt the user wrote, which a rewind can go back to."""
    if not isinstance(message, HumanMessage) or attachment_of(message) is not None:
        return False
    content = message.content
    if isinstance(content, list) and content and isinstance(content[0], dict) and (
            content[0].get("type") == "tool_result"):
        return False
    if is_synthetic_message(message):
        return False
    if _flag(message, "is_meta"):
        return False
    if _flag(message, "is_compact_summary") or _flag(message, "is_visible_in_transcript_only"):
        return False
    if isinstance(content, str):
        message_text = content.strip()
    else:
        last = content[-1] if content else None
        message_text = str(last.get("text", "")).strip() if isinstance(last, dict) and last.get("type") == "text" \
            else ""
    # Messages the user did not write: command output, task notifications, ticks.
    return not any(marker in message_text for marker in _NOT_USER_AUTHORED)


__all__ = [
    "BASH_STDERR_TAG",
    "BASH_STDOUT_TAG",
    "LOCAL_COMMAND_STDERR_TAG",
    "LOCAL_COMMAND_STDOUT_TAG",
    "SYNTHETIC_MESSAGES",
    "TASK_NOTIFICATION_TAG",
    "TEAMMATE_MESSAGE_TAG",
    "TICK_TAG",
    "is_synthetic_message",
    "selectable_user_messages_filter",
]
