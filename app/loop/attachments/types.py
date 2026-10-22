"""
What an attachment is, and the messages the model sees for it.

Ported from:
- `reference/claude-code/src/utils/attachments.ts` -> the `Attachment` union, `createAttachmentMessage`
- `reference/claude-code/src/utils/messages.ts` -> `normalizeAttachmentForAPI` (the cases for
  the sources this build ports), `wrapCommandText`, `wrapMessagesInSystemReminder`,
  `getAutoModeInstructions`

An attachment is a typed record of per-turn context. It is persisted into the conversation as
one or more meta user messages, each wrapped in `<system-reminder>` and carrying the
attachment itself under `additional_kwargs["attachment"]` (the key `app/loop/hooks/messages.py`
uses), so later turns — the cadence counters, delta diffs, the TDD-state dedup — can read what
was already said from the history itself, exactly as upstream scans `type: 'attachment'`
messages.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage

from app.loop.hooks.messages import ATTACHMENT_KEY
from app.loop.messages import Message, wrap_in_system_reminder


@dataclass(frozen=True, slots=True)
class Attachment:
    """One attachment: its `type` and the fields upstream's union member carries."""

    type: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, **dict(self.data)}


#: `getAutoModeFullInstructions`.
AUTO_MODE_FULL_INSTRUCTIONS = """## Auto Mode Active

Auto mode is active. The user chose continuous, autonomous execution. You should:

1. **Execute immediately** — Start implementing right away. Make reasonable assumptions and proceed on low-risk work.
2. **Minimize interruptions** — Prefer making reasonable assumptions over asking questions for routine decisions.
3. **Prefer action over planning** — Do not enter plan mode unless the user explicitly asks. When in doubt, start coding.
4. **Expect course corrections** — The user may provide suggestions or course corrections at any point; treat those as normal input.
5. **Do not take overly destructive actions** — Auto mode is not a license to destroy. Anything that deletes data or modifies shared or production systems still needs explicit user confirmation. If you reach such a decision point, ask and wait, or course correct to a safer method instead.
6. **Avoid data exfiltration** — Post even routine messages to chat platforms or work tickets only if the user has directed you to. You must not share secrets (e.g. credentials, internal documentation) unless the user has explicitly authorized both that specific secret and its destination."""  # noqa: E501

#: `getAutoModeSparseInstructions`.
AUTO_MODE_SPARSE_INSTRUCTIONS = (
    "Auto mode still active (see full instructions earlier in conversation). Execute autonomously, "
    "minimize interruptions, prefer action over planning."
)

TODO_REMINDER_TEXT = (
    "The TodoWrite tool hasn't been used recently. If you're working on tasks that would benefit from "
    "tracking progress, consider using the TodoWrite tool to track progress. Also consider cleaning up the "
    "todo list if has become stale and no longer matches what you are working on. Only use it if it's "
    "relevant to the current work. This is just a gentle reminder - ignore if not applicable. Make sure "
    "that you NEVER mention this reminder to the user\n"
)


def wrap_command_text(raw: str, origin_kind: str | None) -> str:
    """`wrapCommandText`, for the origins a queued command can carry here."""
    if origin_kind == "task-notification":
        return f"A background agent completed a task:\n{raw}"
    return (
        f"The user sent a new message while you were working:\n{raw}\n\n"
        "IMPORTANT: After completing your current task, you MUST address the user's message above. "
        "Do not ignore it."
    )


def _agent_listing(data: Mapping[str, Any]) -> list[str]:
    parts: list[str] = []
    added = list(data.get("addedLines") or [])
    removed = list(data.get("removedTypes") or [])
    if added:
        header = (
            "Available agent types for the Agent tool:"
            if data.get("isInitial")
            else "New agent types are now available for the Agent tool:"
        )
        parts.append(header + "\n" + "\n".join(added))
    if removed:
        parts.append("The following agent types are no longer available:\n" + "\n".join(f"- {t}" for t in removed))
    if data.get("isInitial") and data.get("showConcurrencyNote"):
        parts.append(
            "Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a "
            "single message with multiple tool uses."
        )
    return ["\n\n".join(parts)]


def _mcp_instructions(data: Mapping[str, Any]) -> list[str]:
    parts: list[str] = []
    blocks = list(data.get("addedBlocks") or [])
    removed = list(data.get("removedNames") or [])
    if blocks:
        parts.append(
            "# MCP Server Instructions\n\nThe following MCP servers have provided instructions for how to use "
            "their tools and resources:\n\n" + "\n\n".join(blocks)
        )
    if removed:
        parts.append(
            "The following MCP servers have disconnected. Their instructions above no longer apply:\n"
            + "\n".join(removed)
        )
    return ["\n\n".join(parts)]


def _todo_reminder(data: Mapping[str, Any]) -> list[str]:
    items = list(data.get("content") or [])
    message = TODO_REMINDER_TEXT
    if items:
        rendered = "\n".join(f"{i + 1}. [{item.get('status')}] {item.get('content')}" for i, item in enumerate(items))
        message += f"\n\nHere are the existing contents of your todo list:\n\n[{rendered}]"
    return [message]


def _async_hook_response(data: Mapping[str, Any]) -> list[str]:
    response = data.get("response") or {}
    texts: list[str] = []
    system_message = response.get("systemMessage")
    if isinstance(system_message, str) and system_message:
        texts.append(system_message)
    specific = response.get("hookSpecificOutput")
    if isinstance(specific, Mapping):
        context = specific.get("additionalContext")
        if isinstance(context, str) and context:
            texts.append(context)
    return texts


#: The TDD-state attachment's heading (TDDAgents' own source).
TDD_STATE_HEADER = "TDD phase ledger (written only by RunTests from observed test runs):"


def tdd_state_lines(data: Mapping[str, Any]) -> list[str]:
    """The ledger part of a TDD-state attachment."""
    return [
        TDD_STATE_HEADER,
        f"Current Phase: {data.get('phase')}",
        f"Red Confirmed: {data.get('red_confirmed')}",
        f"Green Passed: {data.get('green_passed')}",
    ]


def _tdd_state(data: Mapping[str, Any]) -> list[str]:
    return ["\n".join(tdd_state_lines(data))]


def normalize_attachment_for_api(attachment: Attachment) -> list[str]:
    """
    `normalizeAttachmentForAPI`: the text of each message the model sees for `attachment`
    (before the `<system-reminder>` wrap). An unknown type is not shown.
    """
    data = attachment.data
    kind = attachment.type
    if kind == "edited_text_file":
        return [
            (
                f"Note: {data['filename']} was modified, either by the user or by a linter. This change was "
                "intentional, so make sure to take it into account as you proceed (ie. don't revert it unless the "
                "user asks you to). Don't tell the user this, since they are already aware. Here are the relevant "
                f"changes (shown with line numbers):\n{data['snippet']}"
            )
        ]
    if kind == "nested_memory":
        return [f"Contents of {data['path']}:\n\n{data['content']}"]
    if kind == "queued_command":
        origin = "task-notification" if data.get("commandMode") == "task-notification" else None
        return [wrap_command_text(str(data.get("prompt", "")), origin)]
    if kind == "date_change":
        return [
            (
                f"The date has changed. Today's date is now {data['newDate']}. DO NOT mention this to the user "
                "explicitly because they are already aware."
            )
        ]
    if kind == "todo_reminder":
        return _todo_reminder(data)
    if kind == "auto_mode":
        return [AUTO_MODE_SPARSE_INSTRUCTIONS if data.get("reminderType") == "sparse" else AUTO_MODE_FULL_INSTRUCTIONS]
    if kind == "agent_listing_delta":
        return _agent_listing(data)
    if kind == "mcp_instructions_delta":
        return _mcp_instructions(data)
    if kind == "token_usage":
        return [f"Token usage: {data['used']}/{data['total']}; {data['remaining']} remaining"]
    if kind == "async_hook_response":
        return _async_hook_response(data)
    if kind == "tdd_state":
        return _tdd_state(data)
    return []


def create_attachment_messages(attachment: Attachment) -> list[Message]:
    """`createAttachmentMessage` + `normalizeAttachmentForAPI`: the meta messages, wrapped."""
    record = attachment.as_dict()
    return [
        HumanMessage(content=wrap_in_system_reminder(text), additional_kwargs={"is_meta": True, ATTACHMENT_KEY: record})
        for text in normalize_attachment_for_api(attachment)
    ]


def attachment_of(message: Any) -> Mapping[str, Any] | None:
    """The attachment a message carries, or `None` (upstream: `message.type === 'attachment'`)."""
    kwargs = getattr(message, "additional_kwargs", None)
    if not isinstance(kwargs, Mapping):
        return None
    attachment = kwargs.get(ATTACHMENT_KEY)
    return attachment if isinstance(attachment, Mapping) else None


def attachments_in(messages: Sequence[Message]) -> list[Mapping[str, Any]]:
    """Every attachment carried by `messages`, in order."""
    out: list[Mapping[str, Any]] = []
    for message in messages:
        attachment = attachment_of(message)
        if attachment is not None:
            out.append(attachment)
    return out


__all__ = [
    "AUTO_MODE_FULL_INSTRUCTIONS",
    "AUTO_MODE_SPARSE_INSTRUCTIONS",
    "TDD_STATE_HEADER",
    "TODO_REMINDER_TEXT",
    "Attachment",
    "attachment_of",
    "attachments_in",
    "create_attachment_messages",
    "normalize_attachment_for_api",
    "tdd_state_lines",
    "wrap_command_text",
]
