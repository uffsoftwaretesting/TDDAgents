"""
The loop's message currency, declared in exactly one place.

`Message` is an alias rather than a class because the loop does not own the message
format — the model seam does. Today every agent module, the chat-model factory and the
LangGraph reducers all speak `langchain_core.messages.BaseMessage`, so adopting a second
vocabulary here would buy nothing and cost an adapter at every boundary.

The alias exists so that decision stays revisable: if Part F1 moves the model call onto a
provider SDK, this line is the whole migration surface, and every signature that mentions
`Message` follows it. Import `Message` from here rather than importing `BaseMessage`
directly, or that property is lost the first time someone shortcuts it.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage
from langchain_core.messages.tool import ToolCall

#: What a conversation is made of, as far as the loop is concerned.
Message = BaseMessage

__all__ = ["Message", "ToolCall", "tool_calls_in"]


def tool_calls_in(message: Message) -> tuple[ToolCall, ...]:
    """
    The tool calls a message is asking for, or nothing.

    Upstream reads this by filtering an assistant message's content blocks for
    `type === 'tool_use'` (`reference/claude-code/src/query.ts`, where a non-empty result
    sets `needsFollowUp`). The LangChain equivalent is `AIMessage.tool_calls`, which is
    already the parsed form.

    Read through `getattr` rather than an `isinstance` check because the decision the loop
    makes is "did this message ask for anything", and a message type that grows tool calls
    later — or a provider adapter that is not an `AIMessage` — should answer it the same
    way. A message without the attribute, or with it set to nothing, simply is not asking:
    both must answer "no calls" rather than raise, because this runs against every message
    a model streams back and one of them raising would abort the turn.
    """
    calls: list[ToolCall] | None = getattr(message, "tool_calls", None)
    return tuple(calls or ())
