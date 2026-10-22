"""
The `todo_reminders` and `auto_mode` sources.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `TODO_REMINDER_CONFIG`,
`getTodoReminderTurnCounts`, `getTodoReminderAttachments`, `AUTO_MODE_ATTACHMENT_CONFIG`,
`getAutoModeAttachmentTurnCount`, `countAutoModeAttachmentsSinceLastExit`,
`getAutoModeAttachments`.

* **todo** — when the `TodoWrite` tool is in the pool and neither a `TodoWrite` call nor a
  reminder happened in the last 10 assistant turns (thinking-only messages do not count), the
  current list is attached with a gentle reminder.
* **auto mode** — while the mode is `auto`: a reminder on the first turn, then again after 5
  human turns; every 5th reminder since the last exit is the full one, the rest are sparse.

Not ported: `task_reminder` (Todo v2 tools are absent), the Brief-tool exemption (no Brief
tool), `plan` mode with auto active, and `auto_mode_exit` — its trigger is upstream's
`transitionPermissionMode` flag, and no mode transition in this build sets one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from app.loop.attachments.types import Attachment, attachment_of
from app.loop.messages import Message, tool_calls_in
from app.loop.permissions.types import PermissionMode

#: `TODO_REMINDER_CONFIG`.
TODO_REMINDER_CONFIG = SimpleNamespace(TURNS_SINCE_WRITE=10, TURNS_BETWEEN_REMINDERS=10)
#: `AUTO_MODE_ATTACHMENT_CONFIG`.
AUTO_MODE_ATTACHMENT_CONFIG = SimpleNamespace(TURNS_BETWEEN_ATTACHMENTS=5, FULL_REMINDER_EVERY_N_ATTACHMENTS=5)

TODO_WRITE_TOOL_NAME = "TodoWrite"


def _is_assistant(message: Any) -> bool:
    return getattr(message, "type", None) == "ai"


def is_thinking_message(message: Any) -> bool:
    """`isThinkingMessage`: an assistant message whose content is only thinking blocks."""
    content = getattr(message, "content", None)
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(block, Mapping) and block.get("type") in ("thinking", "redacted_thinking") for block in content
    )


def get_todo_reminder_turn_counts(messages: Sequence[Message]) -> tuple[int, int]:
    """`getTodoReminderTurnCounts`: assistant turns since the last TodoWrite / reminder."""
    last_write = -1
    last_reminder = -1
    since_write = 0
    since_reminder = 0
    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        if _is_assistant(message):
            if is_thinking_message(message):
                continue
            if last_write == -1 and any(call.get("name") == TODO_WRITE_TOOL_NAME for call in tool_calls_in(message)):
                last_write = i
            if last_write == -1:
                since_write += 1
            if last_reminder == -1:
                since_reminder += 1
        elif last_reminder == -1 and (attachment_of(message) or {}).get("type") == "todo_reminder":
            last_reminder = i
        if last_write != -1 and last_reminder != -1:
            break
    return since_write, since_reminder


def _has_tool(context: Any, name: str) -> bool:
    for tool in getattr(context, "tools", ()) or ():
        if getattr(tool, "name", None) == name or name in getattr(tool, "aliases", ()):
            return True
    return False


def get_todo_reminder_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getTodoReminderAttachments`."""
    if not _has_tool(context, TODO_WRITE_TOOL_NAME) or not messages:
        return []
    since_write, since_reminder = get_todo_reminder_turn_counts(messages)
    if (
        since_write < TODO_REMINDER_CONFIG.TURNS_SINCE_WRITE
        or since_reminder < TODO_REMINDER_CONFIG.TURNS_BETWEEN_REMINDERS
    ):
        return []
    from app.loop.tools.todo import read_todos

    todos = [item.to_dict() for item in read_todos(context)]
    return [Attachment("todo_reminder", {"content": todos, "itemCount": len(todos)})]


def _is_human_turn(message: Any) -> bool:
    if getattr(message, "type", None) != "human":
        return False
    kwargs = getattr(message, "additional_kwargs", None) or {}
    return not kwargs.get("is_meta")


def get_auto_mode_attachment_turn_count(messages: Sequence[Message]) -> tuple[int, bool]:
    """`getAutoModeAttachmentTurnCount`: human turns since the last reminder, and whether one exists."""
    turns = 0
    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        kind = (attachment_of(message) or {}).get("type")
        if kind == "auto_mode":
            return turns, True
        if kind == "auto_mode_exit":
            return turns, False
        if _is_human_turn(message):
            turns += 1
    return turns, False


def count_auto_mode_attachments_since_last_exit(messages: Sequence[Message]) -> int:
    """`countAutoModeAttachmentsSinceLastExit`."""
    count = 0
    for i in range(len(messages) - 1, -1, -1):
        kind = (attachment_of(messages[i]) or {}).get("type")
        if kind == "auto_mode_exit":
            break
        if kind == "auto_mode":
            count += 1
    return count


def _mode(context: Any) -> Any:
    try:
        return context.get_app_state().tool_permission_context.mode
    except Exception:  # noqa: BLE001 - a context without permissions is not in auto mode
        return None


def get_auto_mode_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getAutoModeAttachments`."""
    if _mode(context) != PermissionMode.AUTO:
        return []
    if messages:
        turns, found = get_auto_mode_attachment_turn_count(messages)
        if found and turns < AUTO_MODE_ATTACHMENT_CONFIG.TURNS_BETWEEN_ATTACHMENTS:
            return []
    count = count_auto_mode_attachments_since_last_exit(messages) + 1
    reminder = "full" if count % AUTO_MODE_ATTACHMENT_CONFIG.FULL_REMINDER_EVERY_N_ATTACHMENTS == 1 else "sparse"
    return [Attachment("auto_mode", {"reminderType": reminder})]


__all__ = [
    "AUTO_MODE_ATTACHMENT_CONFIG",
    "TODO_REMINDER_CONFIG",
    "count_auto_mode_attachments_since_last_exit",
    "get_auto_mode_attachment_turn_count",
    "get_auto_mode_attachments",
    "get_todo_reminder_attachments",
    "get_todo_reminder_turn_counts",
    "is_thinking_message",
]
