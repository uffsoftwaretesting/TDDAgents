"""
Token counts from the provider's own counting endpoint.

Ported from `reference/claude-code/src/services/tokenEstimation.ts` ->
`countMessagesTokensWithAPI`: the run's model counts the messages and the tool schemas; with
tools and no messages a dummy `foo` user message is sent, as upstream does, because the endpoint
needs one; any failure is logged and reads as `None`.

**TDDAgents** (user decision 2026-11-01): the count runs on Claude **and** OpenAI runs (Anthropic's
`messages.count_tokens`, OpenAI's `responses.input_tokens.count`, through the provider seam
`app/utils/chat_model_factory.py`). Where the endpoint gives no count (another provider, an error),
`api_token_counter` falls back to the rough estimate (`roughTokenCountEstimation`) rather than to
upstream's `countTokensViaHaikuFallback`, which bills a one-token `messages.create` per count.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain_core.messages import HumanMessage

from app.utils.messages import Message

logger = logging.getLogger(__name__)

#: The placeholder message a tools-only count sends (`countMessagesTokensWithAPI`).
TOOLS_ONLY_PLACEHOLDER = "foo"

#: `(provider, model, messages, tools) -> count`: the seam's `count_input_tokens`.
ProviderCounter = Callable[[str, str, Sequence[Message], Sequence[Mapping[str, Any]]], Awaitable[int | None]]
#: `analyze_context.TokenCounter`.
TokenCounter = Callable[[Sequence[Message], Sequence[Mapping[str, Any]]], Awaitable[int | None]]


async def _provider_count(provider: str, model: str, messages: Sequence[Message],
                          tools: Sequence[Mapping[str, Any]]) -> int | None:
    from app.utils.chat_model_factory import count_input_tokens

    return await count_input_tokens(provider, model, messages, tools)


async def count_messages_tokens_with_api(
    messages: Sequence[Message], tools: Sequence[Mapping[str, Any]], *, model: str, provider: str | None = None,
    counter: ProviderCounter | None = None,
) -> int | None:
    """`countMessagesTokensWithAPI`: the provider's count, or `None` when it has none or fails."""
    from app.config.config import Config

    to_send: Sequence[Message] = messages if messages else [HumanMessage(content=TOOLS_ONLY_PLACEHOLDER)]
    try:
        count = await (counter or _provider_count)(provider or Config.CHAT_MODEL, model, to_send, tools)
    except Exception:  # noqa: BLE001 - upstream's `logError(error); return null`
        logger.exception("countMessagesTokensWithAPI failed")
        return None
    return count if isinstance(count, int) else None


def api_token_counter(model: str, *, provider: str | None = None,
                      counter: ProviderCounter | None = None) -> TokenCounter:
    """The `/context` counter: the API's count, else the rough estimate."""
    from app.utils.analyze_context import rough_count_tokens

    async def count(messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]) -> int | None:
        counted = await count_messages_tokens_with_api(messages, tools, model=model, provider=provider,
                                                       counter=counter)
        return counted if counted is not None else await rough_count_tokens(messages, tools)

    return count


__all__ = [
    "TOOLS_ONLY_PLACEHOLDER",
    "api_token_counter",
    "count_messages_tokens_with_api",
]
