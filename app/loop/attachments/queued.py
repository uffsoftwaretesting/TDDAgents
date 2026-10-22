"""
The `queued_commands` and `async_hook_responses` sources.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getQueuedCommandAttachments`,
`getAsyncHookResponseAttachments`. The only queued commands in a headless run are the
`asyncRewake` notifications a background hook enqueues (`enqueuePendingNotification`, mode
`task-notification`); they live in the run's `AsyncHookRegistry`, as do finished background
hooks' responses. Both are drained here, once, between turns.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.loop.attachments.types import Attachment
from app.loop.messages import Message


def _registry(context: Any) -> Any:
    return getattr(getattr(context, "hook_dispatcher", None), "registry", None)


def get_queued_command_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getQueuedCommandAttachments`: every pending notification, drained."""
    registry = _registry(context)
    if registry is None:
        return []
    return [
        Attachment("queued_command", {"prompt": note, "commandMode": "task-notification"})
        for note in registry.drain_notifications()
    ]


async def get_async_hook_response_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getAsyncHookResponseAttachments`."""
    registry = _registry(context)
    if registry is None:
        return []
    out: list[Attachment] = []
    for response in await registry.check_for_responses():
        record = response.attachment()
        record.pop("type", None)
        out.append(Attachment("async_hook_response", record))
    return out


__all__ = ["get_async_hook_response_attachments", "get_queued_command_attachments"]
