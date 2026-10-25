"""
The `agent_pending_messages` source (Phase 8).

Ported from `reference/claude-code/src/utils/attachments.ts` ->
`getAgentPendingMessageAttachments`: a background worker drains the messages `SendMessage`
queued on its task (`queuePendingMessage`) at its next tool round, each becoming a meta
`queued_command` from the coordinator. The main thread has no agent id and drains nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.loop.attachments.types import Attachment
from app.loop.messages import Message
from app.loop.tasks.local_agent_task import drain_pending_messages


def get_agent_pending_message_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """
    `getAgentPendingMessageAttachments`. Upstream drains through `setAppStateForTasks ??
    setAppState`; a background worker's `set_app_state` here already lets task writes reach the
    root store (`agents/run_agent.py`), so it is that one setter.
    """
    agent_id = getattr(context, "agent_id", None)
    if not agent_id:
        return []
    drained = drain_pending_messages(agent_id, context.get_app_state, context.set_app_state)
    return [
        Attachment("queued_command", {"prompt": msg, "origin": {"kind": "coordinator"}, "isMeta": True})
        for msg in drained
    ]


__all__ = ["get_agent_pending_message_attachments"]
