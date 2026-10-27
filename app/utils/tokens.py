"""
Token counting substrate for TDDAgents context management.

Ported from upstream Claude Code's `roughTokenCountEstimation`
(`Math.ceil(text.length / 4)`). Fast, offline, with zero external dependencies.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.utils.messages import Message, tool_calls_in


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


# ── Phase 5: upstream's counting with the last API response, and the window ────
#
# Ported from:
# - `reference/claude-code/src/services/tokenEstimation.ts` -> `roughTokenCountEstimation`,
#   `roughTokenCountEstimationForMessages` (and its per-message / per-block helpers)
# - `reference/claude-code/src/utils/tokens.ts` -> `getTokenUsage`, `getTokenCountFromUsage`,
#   `tokenCountWithEstimation`
# - `reference/claude-code/src/utils/context.ts` -> `MODEL_CONTEXT_WINDOW_DEFAULT`,
#   `COMPACT_MAX_OUTPUT_TOKENS`, `getContextWindowForModel`, `getModelMaxOutputTokens`
# - `reference/claude-code/src/services/api/claude.ts` -> `getMaxOutputTokensForModel`
# - `reference/claude-code/src/utils/envValidation.ts` -> `validateBoundedIntEnvVar`
# - `reference/claude-code/src/services/compact/autoCompact.ts` -> `getEffectiveContextWindowSize`
#
# The heuristics above (`estimate_*`) predate this port and stay for their callers; these
# are upstream's, which round with `Math.round` and add no per-message envelope.

#: `MODEL_CONTEXT_WINDOW_DEFAULT`.
MODEL_CONTEXT_WINDOW_DEFAULT = 200_000
#: `COMPACT_MAX_OUTPUT_TOKENS`.
COMPACT_MAX_OUTPUT_TOKENS = 20_000
#: `autoCompact.ts` -> `MAX_OUTPUT_TOKENS_FOR_SUMMARY`: the output reserved for a summary.
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000
#: `context.ts` -> `MAX_OUTPUT_TOKENS_DEFAULT` / `MAX_OUTPUT_TOKENS_UPPER_LIMIT`.
MAX_OUTPUT_TOKENS_DEFAULT = 32_000
MAX_OUTPUT_TOKENS_UPPER_LIMIT = 64_000
#: `ONE_MILLION_CONTEXT` (`has1mContext`'s window).
ONE_MILLION_CONTEXT = 1_000_000
#: A media block's fixed estimate (`roughTokenCountEstimationForBlock`, `IMAGE_MAX_TOKEN_SIZE`).
MEDIA_BLOCK_TOKENS = 2000

_ONE_M_SUFFIX = re.compile(r"\[1m\]", re.IGNORECASE)
_LEADING_INT = re.compile(r"^\s*([+-]?\d+)")
_MEDIA_TYPES = frozenset({"image", "image_url", "document"})


def _js_round(value: float) -> int:
    """`Math.round`: halves go up, unlike Python's banker's rounding."""
    return math.floor(value + 0.5)


def _parse_int(value: str | None) -> int | None:
    """JavaScript `parseInt(value, 10)`: the leading integer, or `None` (NaN)."""
    if value is None:
        return None
    match = _LEADING_INT.match(value)
    return int(match.group(1)) if match else None


def _json_compact(value: Any) -> str:
    """`JSON.stringify` without spacing."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def rough_token_count_estimation(content: str, bytes_per_token: float = 4) -> int:
    """`roughTokenCountEstimation`: `Math.round(content.length / bytesPerToken)`."""
    return _js_round(len(content) / bytes_per_token)


def _rough_for_block(block: Any) -> int:
    if isinstance(block, str):
        return rough_token_count_estimation(block)
    if not isinstance(block, dict):
        return rough_token_count_estimation(str(block))
    kind = block.get("type")
    if kind == "text":
        return rough_token_count_estimation(str(block.get("text") or ""))
    if kind in _MEDIA_TYPES:
        return MEDIA_BLOCK_TOKENS
    if kind == "tool_result":
        return _rough_for_content(block.get("content"))
    if kind == "tool_use":
        return rough_token_count_estimation(str(block.get("name", "")) + _json_compact(block.get("input") or {}))
    if kind == "thinking":
        return rough_token_count_estimation(str(block.get("thinking") or ""))
    if kind == "redacted_thinking":
        return rough_token_count_estimation(str(block.get("data") or ""))
    return rough_token_count_estimation(_json_compact(block))


def _rough_for_content(content: Any) -> int:
    if not content:
        return 0
    if isinstance(content, str):
        return rough_token_count_estimation(content)
    return sum(_rough_for_block(block) for block in content)


def rough_token_count_estimation_for_message(message: Message) -> int:
    """
    `roughTokenCountEstimationForMessage`. Upstream counts user, assistant and attachment
    messages; here those are `human`, `ai` and `tool` (a tool result is a user-message block
    upstream). An assistant's tool calls are its `tool_use` blocks.
    """
    kind = getattr(message, "type", None)
    if kind not in ("human", "ai", "tool"):
        return 0
    total = _rough_for_content(getattr(message, "content", None))
    if kind == "ai":
        for call in tool_calls_in(message):
            total += rough_token_count_estimation(str(call.get("name", "")) + _json_compact(call.get("args") or {}))
    return total


def rough_token_count_estimation_for_messages(messages: Sequence[Message]) -> int:
    """`roughTokenCountEstimationForMessages`."""
    return sum(rough_token_count_estimation_for_message(m) for m in messages)


def _is_synthetic(message: Message) -> bool:
    """`SYNTHETIC_MESSAGES` / `SYNTHETIC_MODEL`: an API-error message the loop made up."""
    kwargs = getattr(message, "additional_kwargs", None)
    return isinstance(kwargs, dict) and (kwargs.get("is_api_error_message") is True or bool(kwargs.get("api_error")))


def get_token_usage(message: Message) -> dict[str, Any] | None:
    """`getTokenUsage`: a real assistant response's usage, else `None`."""
    if getattr(message, "type", None) != "ai" or _is_synthetic(message):
        return None
    usage = getattr(message, "usage_metadata", None)
    return dict(usage) if usage is not None else None


def get_token_count_from_usage(usage: dict[str, Any]) -> int:
    """
    `getTokenCountFromUsage`: input + cache creation + cache read + output. LangChain's
    `input_tokens` already includes the cache tokens (`input_token_details` itemises them),
    so the sum is `input_tokens + output_tokens`.
    """
    return int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)


def _assistant_message_id(message: Message) -> str | None:
    if getattr(message, "type", None) != "ai" or _is_synthetic(message):
        return None
    message_id = getattr(message, "id", None)
    return str(message_id) if message_id else None


def token_count_with_estimation(messages: Sequence[Message]) -> int:
    """
    `tokenCountWithEstimation`: the last real API response's usage, plus a rough estimate of
    everything after it. Records split from one response (same id) are walked back first, so
    results interleaved between them are counted.
    """
    i = len(messages) - 1
    while i >= 0:
        usage = get_token_usage(messages[i])
        if usage is not None:
            response_id = _assistant_message_id(messages[i])
            if response_id:
                j = i - 1
                while j >= 0:
                    prior_id = _assistant_message_id(messages[j])
                    if prior_id == response_id:
                        i = j
                    elif prior_id is not None:
                        break
                    j -= 1
            return get_token_count_from_usage(usage) + rough_token_count_estimation_for_messages(messages[i + 1:])
        i -= 1
    return rough_token_count_estimation_for_messages(messages)


def _env_truthy(name: str) -> bool:
    value = os.environ.get(name)
    return value is not None and value.strip().lower() in ("1", "true", "yes", "on")


def has_1m_context(model: str) -> bool:
    """`has1mContext`: an explicit `[1m]` suffix, unless `TDDAGENTS_DISABLE_1M_CONTEXT`."""
    if _env_truthy("TDDAGENTS_DISABLE_1M_CONTEXT"):
        return False
    return _ONE_M_SUFFIX.search(model) is not None


def get_context_window_for_model(model: str) -> int:
    """
    `getContextWindowForModel`, external path: the `[1m]` opt-in, else the default. There is
    no model-capability API, beta header, experiment or ant table here, so every model this
    build has no capability for gets `MODEL_CONTEXT_WINDOW_DEFAULT`, as upstream's does.
    """
    if has_1m_context(model):
        return ONE_MILLION_CONTEXT
    return MODEL_CONTEXT_WINDOW_DEFAULT


def get_model_max_output_tokens(model: str) -> tuple[int, int]:
    """`getModelMaxOutputTokens` -> `(default, upper_limit)` by canonical (lowercased) name."""
    m = model.lower()
    if "opus-4-6" in m:
        return 64_000, 128_000
    if "sonnet-4-6" in m:
        return 32_000, 128_000
    if "opus-4-5" in m or "sonnet-4" in m or "haiku-4" in m:
        return 32_000, 64_000
    if "opus-4-1" in m or "opus-4" in m:
        return 32_000, 32_000
    if "claude-3-opus" in m:
        return 4_096, 4_096
    if "claude-3-sonnet" in m:
        return 8_192, 8_192
    if "claude-3-haiku" in m:
        return 4_096, 4_096
    if "3-5-sonnet" in m or "3-5-haiku" in m:
        return 8_192, 8_192
    if "3-7-sonnet" in m:
        return 32_000, 64_000
    return MAX_OUTPUT_TOKENS_DEFAULT, MAX_OUTPUT_TOKENS_UPPER_LIMIT


def validate_bounded_int_env_var(value: str | None, default: int, upper_limit: int) -> int:
    """`validateBoundedIntEnvVar(...).effective`."""
    if not value:
        return default
    parsed = _parse_int(value)
    if parsed is None or parsed <= 0:
        return default
    return min(parsed, upper_limit)


def get_max_output_tokens_for_model(model: str) -> int:
    """
    `getMaxOutputTokensForModel`: the model's default, overridable by
    `TDDAGENTS_MAX_OUTPUT_TOKENS` (upstream's `CLAUDE_CODE_MAX_OUTPUT_TOKENS`) up to its upper
    limit. Upstream's 8k slot-reservation cap is a GrowthBook flag that defaults off for
    third-party providers, so it is off here.
    """
    default, upper = get_model_max_output_tokens(model)
    return validate_bounded_int_env_var(os.environ.get("TDDAGENTS_MAX_OUTPUT_TOKENS"), default, upper)


def get_effective_context_window_size(model: str) -> int:
    """
    `getEffectiveContextWindowSize`: the window minus the output reserved for a summary,
    capped by `TDDAGENTS_AUTO_COMPACT_WINDOW` (upstream's `CLAUDE_CODE_AUTO_COMPACT_WINDOW`).
    """
    reserved = min(get_max_output_tokens_for_model(model), MAX_OUTPUT_TOKENS_FOR_SUMMARY)
    window = get_context_window_for_model(model)
    override = _parse_int(os.environ.get("TDDAGENTS_AUTO_COMPACT_WINDOW"))
    if override is not None and override > 0:
        window = min(window, override)
    return window - reserved
