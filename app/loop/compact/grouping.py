"""
API-round grouping for compaction retries.

Ported from `reference/claude-code/src/services/compact/grouping.ts` ->
`groupMessagesByApiRound`: a new group starts at every assistant message whose id differs
from the previous assistant's, so one model response (several messages sharing an id) and
the results that answer it stay together.

*Divergence:* an assistant message with no id always starts a new group. Upstream's messages
always carry an id; LangChain's may not, and treating "no id" as "the same response" would
merge whole turns into one group.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.loop.messages import Message


def _is_assistant(message: Any) -> bool:
    return getattr(message, "type", None) == "ai"


def group_messages_by_api_round(messages: Sequence[Message]) -> list[list[Message]]:
    """`groupMessagesByApiRound`."""
    groups: list[list[Message]] = []
    current: list[Message] = []
    last_assistant_id: str | None = None
    for message in messages:
        if _is_assistant(message):
            message_id = getattr(message, "id", None)
            new_round = message_id is None or message_id != last_assistant_id
            if new_round and current:
                groups.append(current)
                current = [message]
            else:
                current.append(message)
            last_assistant_id = message_id
        else:
            current.append(message)
    if current:
        groups.append(current)
    return groups


__all__ = ["group_messages_by_api_round"]
