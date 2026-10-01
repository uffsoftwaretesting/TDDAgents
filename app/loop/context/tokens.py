"""
Token counting substrate for TDDAgents context management.

Ported from upstream Claude Code's `roughTokenCountEstimation`
(`Math.ceil(text.length / 4)`). Fast, offline, with zero external dependencies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from app.loop.messages import Message, tool_calls_in


def estimate_string_tokens(text: str | None) -> int:
    """
    Estimate token count for a raw string.

    Upstream Claude Code: `Math.ceil(text.length / 4)`.
    Empty or None returns 0.
    """
    if not text:
        return 0
    return (len(text) + 3) // 4


def estimate_block_tokens(block: Any) -> int:
    """
    Estimate token count for a content block.

    Handles strings, dicts (text, thinking, tool_use, tool_result), etc.
    """
    if isinstance(block, str):
        return estimate_string_tokens(block)
    if isinstance(block, dict):
        block_type = block.get("type")
        if block_type == "text":
            return estimate_string_tokens(block.get("text", ""))
        if block_type == "thinking":
            return estimate_string_tokens(block.get("thinking", ""))
        if block_type == "tool_use":
            name = block.get("name", "")
            inp = block.get("input", "")
            inp_str = json.dumps(inp) if isinstance(inp, (dict, list)) else str(inp)
            return estimate_string_tokens(name) + estimate_string_tokens(inp_str)
        if block_type == "tool_result":
            content = block.get("content", "")
            content_str = json.dumps(content) if isinstance(content, (dict, list)) else str(content)
            return estimate_string_tokens(content_str)
        # Fallback to json dump
        return estimate_string_tokens(json.dumps(block))
    return estimate_string_tokens(str(block))


def estimate_message_tokens(message: Message) -> int:
    """
    Estimate token count for a single message, including content, tool calls, and message framing.

    Adds 4 tokens per message for API envelope / framing overhead.
    """
    tokens = 4  # Envelope overhead
    content = getattr(message, "content", None)
    if isinstance(content, str):
        tokens += estimate_string_tokens(content)
    elif isinstance(content, (list, tuple)):
        for part in content:
            tokens += estimate_block_tokens(part)

    # Tool calls
    calls = tool_calls_in(message)
    for call in calls:
        tokens += estimate_string_tokens(call.get("name", ""))
        args = call.get("args", {})
        args_str = json.dumps(args) if isinstance(args, (dict, list)) else str(args)
        tokens += estimate_string_tokens(args_str)

    # Thinking in additional_kwargs if present
    additional = getattr(message, "additional_kwargs", None)
    if isinstance(additional, dict) and "thinking" in additional:
        tokens += estimate_string_tokens(str(additional["thinking"]))

    return tokens


def count_messages_tokens(messages: Sequence[Message]) -> int:
    """Estimate total tokens across a sequence of messages."""
    return sum(estimate_message_tokens(m) for m in messages)


@dataclass(frozen=True, slots=True)
class TokenWarningState:
    """Token usage status against context limits."""

    tokens: int
    model_limit: int
    is_warning: bool
    is_blocking_limit: bool


def calculate_token_warning_state(
    tokens: int,
    model_limit: int,
    warning_threshold: float = 0.8,
    blocking_threshold: float = 0.95,
) -> TokenWarningState:
    """Determine whether current token count hits warning or blocking thresholds."""
    if model_limit <= 0:
        return TokenWarningState(
            tokens=tokens,
            model_limit=model_limit,
            is_warning=False,
            is_blocking_limit=False,
        )
    ratio = tokens / model_limit
    return TokenWarningState(
        tokens=tokens,
        model_limit=model_limit,
        is_warning=ratio >= warning_threshold,
        is_blocking_limit=ratio >= blocking_threshold,
    )


class TokenCounter:
    """Token counter interface wrapping upstream estimation logic."""

    def count_string(self, text: str | None) -> int:
        return estimate_string_tokens(text)

    def count_message(self, message: Message) -> int:
        return estimate_message_tokens(message)

    def count_messages(self, messages: Sequence[Message]) -> int:
        return count_messages_tokens(messages)

    def warning_state(
        self,
        tokens: int,
        model_limit: int,
        warning_threshold: float = 0.8,
        blocking_threshold: float = 0.95,
    ) -> TokenWarningState:
        return calculate_token_warning_state(
            tokens, model_limit, warning_threshold, blocking_threshold
        )
