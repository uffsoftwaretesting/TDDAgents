"""
The production summary request: one non-streaming model call, no tools.

Ported from `reference/claude-code/src/services/compact/compact.ts` -> `streamCompactSummary`
(the non-forked path): system prompt `COMPACT_SUMMARIZER_SYSTEM_PROMPT`, the history after the
last boundary with system records dropped (`normalizeMessagesForAPI`), then the summary
request, capped at `max_output_tokens`. A context overflow comes back the way upstream's API
layer reports it — an assistant API-error message starting with
`PROMPT_TOO_LONG_ERROR_MESSAGE`, carrying the provider text as `error_details` — so
`compact_conversation`'s PTL retry can act on it; any other provider error propagates.

*Divergence:* upstream sends `FileReadTool` (and MCP tools under tool search) only to share
the main loop's prompt cache, with tool use refused; this call sends no tools.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage

from app.config.config import Config
from app.services.compact.compact import (
    PROMPT_TOO_LONG_ERROR_MESSAGE,
    CompactSummarizer,
)
from app.services.compact.prompt import COMPACT_SUMMARIZER_SYSTEM_PROMPT
from app.utils.chat_model_factory import get_chat_model
from app.utils.messages import Message

_CONTEXT_OVERFLOW = re.compile(
    r"prompt is too long|maximum context length|context_length_exceeded|context length", re.IGNORECASE
)


def is_context_overflow_error(exc: BaseException) -> bool:
    """A provider error that means the request exceeded the model's context window."""
    return _CONTEXT_OVERFLOW.search(str(exc)) is not None


def build_compact_summarizer(provider: str | None = None, model: str | None = None) -> CompactSummarizer:
    """The production `CompactSummarizer`, on the configured provider and model by default."""

    async def summarize(
        messages: Sequence[Message], summary_request: Message, context: Any, *, max_output_tokens: int
    ) -> Message:
        chat = get_chat_model(
            provider or Config.CHAT_MODEL,
            model=model or Config.MODEL,
            temperature=Config.TEMPERATURE,
            max_tokens=max_output_tokens,
        )
        request = [
            SystemMessage(content=COMPACT_SUMMARIZER_SYSTEM_PROMPT),
            *(m for m in messages if getattr(m, "type", None) != "system"),
            summary_request,
        ]
        try:
            reply: Message = await chat.ainvoke(request)
        except Exception as exc:
            if not is_context_overflow_error(exc):
                raise
            details = str(exc)
            return AIMessage(
                content=f"{PROMPT_TOO_LONG_ERROR_MESSAGE}: {details}",
                additional_kwargs={"is_api_error_message": True, "api_error": "prompt_too_long",
                                   "error_details": details},
            )
        return reply

    return summarize


__all__ = ["build_compact_summarizer", "is_context_overflow_error"]
