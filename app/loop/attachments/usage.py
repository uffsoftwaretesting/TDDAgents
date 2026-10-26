"""
The `token_usage` source.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getTokenUsageAttachment` and
`src/utils/tokens.ts` -> `getTokenUsage`, `getTokenCountFromUsage`,
`tokenCountFromLastAPIResponse`. Off unless `TDDAGENTS_ENABLE_TOKEN_USAGE_ATTACHMENT` is set
(upstream's `CLAUDE_CODE_ENABLE_TOKEN_USAGE_ATTACHMENT`), and main-thread only.

The usage is the provider's own count on the last assistant message (LangChain's
`usage_metadata`: input, output, and the cache read/creation details upstream adds in).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from app.loop.attachments.types import Attachment
from app.loop.messages import Message

TOKEN_USAGE_ENV = "TDDAGENTS_ENABLE_TOKEN_USAGE_ATTACHMENT"


def token_count_from_usage(usage: Mapping[str, Any]) -> int:
    """`getTokenCountFromUsage`: input + cache creation + cache read + output."""
    details = usage.get("input_token_details") or {}
    return (
        int(usage.get("input_tokens") or 0)
        + int(details.get("cache_creation") or 0)
        + int(details.get("cache_read") or 0)
        + int(usage.get("output_tokens") or 0)
    )


def token_count_from_last_api_response(messages: Sequence[Message]) -> int:
    """`tokenCountFromLastAPIResponse`: the last assistant message carrying usage, else 0."""
    for message in reversed(list(messages)):
        if getattr(message, "type", None) != "ai":
            continue
        usage = getattr(message, "usage_metadata", None)
        if isinstance(usage, Mapping):
            return token_count_from_usage(usage)
    return 0


def effective_context_window(model: str) -> int:
    """`getEffectiveContextWindowSize`, from the token module (Phase 5, F1)."""
    import app.loop.context.tokens as tokens

    size: int = tokens.get_effective_context_window_size(model)
    return size


def get_token_usage_attachment(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getTokenUsageAttachment`."""
    if os.environ.get(TOKEN_USAGE_ENV, "").strip().lower() not in ("1", "true", "yes", "on"):
        return []
    from app.config.config import Config

    window = effective_context_window(Config.MODEL)
    used = token_count_from_last_api_response(messages)
    return [Attachment("token_usage", {"used": used, "total": window, "remaining": window - used})]


__all__ = [
    "TOKEN_USAGE_ENV",
    "effective_context_window",
    "get_token_usage_attachment",
    "token_count_from_last_api_response",
    "token_count_from_usage",
]
