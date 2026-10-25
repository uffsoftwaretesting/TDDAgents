"""
The `queued_commands` and `async_hook_responses` sources.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getQueuedCommandAttachments`,
`getAsyncHookResponseAttachments`. Queued commands live in the run's `CommandQueue`
(`ToolContext.command_queue`), fed by background tasks and `asyncRewake` hooks; finished
background hooks' responses live in the run's `AsyncHookRegistry`. Task notifications are
`later` priority, so they are delivered as a turn of their own by the runner, not here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.loop.attachments.types import Attachment
from app.loop.command_queue import QueuedCommand, is_slash_command
from app.loop.messages import Message

#: `attachments.ts` -> `INLINE_NOTIFICATION_MODES`.
INLINE_NOTIFICATION_MODES = frozenset({"prompt", "task-notification"})


def _registry(context: Any) -> Any:
    return getattr(getattr(context, "hook_dispatcher", None), "registry", None)


def get_queued_command_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """
    `getQueuedCommandAttachments` over `query.ts`'s snapshot: the commands at `next` priority or
    above (no Sleep tool here, so never `later`), minus slash commands, scoped by agent — the main
    thread takes what is addressed to no agent, a worker only its own task notifications. Only
    `INLINE_NOTIFICATION_MODES` become attachments, and only those are removed from the queue.
    """
    queue = getattr(context, "command_queue", None)
    if queue is None:
        return []
    agent_id = getattr(context, "agent_id", None)

    def addressed(command: QueuedCommand) -> bool:
        if is_slash_command(command):
            return False
        if agent_id is None:
            return command.agent_id is None
        return command.mode == "task-notification" and command.agent_id == agent_id

    consumed = [c for c in queue.get_commands_by_max_priority("next")
                if addressed(c) and c.mode in INLINE_NOTIFICATION_MODES]
    queue.remove(consumed)
    return [
        Attachment("queued_command", {"prompt": c.value, "source_uuid": c.uuid, "commandMode": c.mode})
        for c in consumed
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
