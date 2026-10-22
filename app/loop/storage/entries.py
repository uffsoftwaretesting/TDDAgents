"""
The transcript entry format (Phase 6).

Ported from `reference/claude-code/src/utils/sessionStorage.ts` -> `insertMessageChain`, which
writes one `TranscriptMessage` per line: the message itself, spread, plus the chain links
(`parentUuid`, `logicalParentUuid` on a compact boundary, `isSidechain`, `agentId`) and the
session stamp (`userType`, `cwd`, `sessionId`, `version`, `gitBranch`) written *after* the
spread so a resumed message is re-stamped.

The port's one structural difference: upstream's messages are plain objects whose fields can
be spread into the entry, ours are LangChain messages. The message travels whole under
`message` in LangChain's own dict form (`message_to_dict`), which round-trips losslessly; the
fields readers branch on (`subtype`, `isMeta`, `isCompactSummary`, `isApiErrorMessage`,
`sourceToolAssistantUUID`) are mirrored at the top level, where upstream has them.

A message's `uuid` lives in `additional_kwargs`, beside the other loop flags (`is_meta`,
`compact_boundary`). It is assigned once, the first time the message is recorded, and is
what `recordTranscript`'s dedup and the `parentUuid` chain are keyed on.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import message_to_dict, messages_from_dict

from app.loop.compact.compact import BOUNDARY_KEY, is_compact_boundary_message
from app.loop.hooks.messages import ATTACHMENT_KEY
from app.loop.messages import Message

#: Where a message's transcript identity is kept.
UUID_KEY = "uuid"

#: `getUserType()` in the external build.
USER_TYPE = "external"

#: `isLoggableMessage`'s opt-in env for hook additional context.
SAVE_HOOK_ADDITIONAL_CONTEXT_ENV = "TDDAGENTS_SAVE_HOOK_ADDITIONAL_CONTEXT"

_TRUTHY = ("1", "true", "yes", "on")

_ENTRY_TYPES = {"human": "user", "tool": "user", "ai": "assistant", "system": "system"}


@dataclass(frozen=True)
class EntryStamp:
    """The session fields `insertMessageChain` stamps on every entry."""

    session_id: str
    cwd: str
    version: str
    git_branch: str | None


def message_uuid(message: Any) -> str | None:
    kwargs = getattr(message, "additional_kwargs", None)
    value = kwargs.get(UUID_KEY) if isinstance(kwargs, Mapping) else None
    return value if isinstance(value, str) else None


def ensure_message_uuid(message: Message, new_uuid: Callable[[], str]) -> str:
    """The message's uuid, assigned on first sight and never changed afterwards."""
    existing = message_uuid(message)
    if existing is not None:
        return existing
    assigned = new_uuid()
    message.additional_kwargs[UUID_KEY] = assigned
    return assigned


def entry_type_of(message: Any) -> str | None:
    """Upstream's `type`: a tool result is a `user` message, as on the API."""
    return _ENTRY_TYPES.get(getattr(message, "type", ""))


def is_loggable_message(message: Any) -> bool:
    """
    `isLoggableMessage` for the external build: attachments are not persisted, except hook
    additional context when `SAVE_HOOK_ADDITIONAL_CONTEXT_ENV` opts in.
    """
    kwargs = getattr(message, "additional_kwargs", None)
    attachment = kwargs.get(ATTACHMENT_KEY) if isinstance(kwargs, Mapping) else None
    if not isinstance(attachment, Mapping):
        return True
    opted_in = os.environ.get(SAVE_HOOK_ADDITIONAL_CONTEXT_ENV, "").strip().lower() in _TRUTHY
    return attachment.get("type") == "hook_additional_context" and opted_in


def iso_timestamp(epoch_seconds: float) -> str:
    """`new Date().toISOString()`: UTC, millisecond precision, `Z` suffix."""
    moment = datetime.fromtimestamp(epoch_seconds, tz=UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_entry(
    message: Message,
    parent_uuid: str | None,
    is_sidechain: bool,
    stamp: EntryStamp,
    timestamp: str,
    *,
    agent_id: str | None = None,
    source_tool_assistant_uuid: str | None = None,
) -> dict[str, Any]:
    """One `TranscriptMessage`. The message must already carry its uuid."""
    kwargs = message.additional_kwargs
    boundary = is_compact_boundary_message(message)
    entry: dict[str, Any] = {
        "parentUuid": None if boundary else parent_uuid,
        "isSidechain": is_sidechain,
    }
    if boundary:
        entry["logicalParentUuid"] = parent_uuid
    if agent_id is not None:
        entry["agentId"] = agent_id
    entry["type"] = entry_type_of(message)
    if boundary:
        entry["subtype"] = "compact_boundary"
        entry["compactMetadata"] = dict(kwargs[BOUNDARY_KEY])
    for flag, key in (("isMeta", "is_meta"), ("isCompactSummary", "is_compact_summary"),
                      ("isApiErrorMessage", "is_api_error_message")):
        if kwargs.get(key) is True:
            entry[flag] = True
    if source_tool_assistant_uuid is not None:
        entry["sourceToolAssistantUUID"] = source_tool_assistant_uuid
    entry["message"] = message_to_dict(message)
    entry["uuid"] = message_uuid(message)
    entry["timestamp"] = timestamp
    entry["userType"] = USER_TYPE
    entry["cwd"] = stamp.cwd
    entry["sessionId"] = stamp.session_id
    entry["version"] = stamp.version
    if stamp.git_branch is not None:
        entry["gitBranch"] = stamp.git_branch
    return entry


def entry_to_message(entry: Mapping[str, Any]) -> Message | None:
    """The message an entry carries, uuid restored; `None` for a non-message entry."""
    payload = entry.get("message")
    if not isinstance(payload, Mapping):
        return None
    try:
        (message,) = messages_from_dict([dict(payload)])
    except (KeyError, TypeError, ValueError):
        return None
    uuid = entry.get("uuid")
    if isinstance(uuid, str):
        message.additional_kwargs[UUID_KEY] = uuid
    return message


__all__ = [
    "SAVE_HOOK_ADDITIONAL_CONTEXT_ENV",
    "USER_TYPE",
    "UUID_KEY",
    "EntryStamp",
    "build_entry",
    "ensure_message_uuid",
    "entry_to_message",
    "entry_type_of",
    "is_loggable_message",
    "iso_timestamp",
    "message_uuid",
]
