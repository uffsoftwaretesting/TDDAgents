"""
Microcompaction: clearing old tool results before a request (time-based path).

Ported from:
- `reference/claude-code/src/services/compact/microCompact.ts` -> `microcompactMessages`,
  `evaluateTimeBasedTrigger`, `maybeTimeBasedMicrocompact`, `collectCompactableToolIds`,
  `calculateToolResultTokens`, `COMPACTABLE_TOOLS`, `TIME_BASED_MC_CLEARED_MESSAGE`
- `reference/claude-code/src/services/compact/timeBasedMCConfig.ts` -> `TimeBasedMCConfig`,
  `TIME_BASED_MC_CONFIG_DEFAULTS`, `getTimeBasedMCConfig`

When the last main-thread assistant message is older than the gap threshold, the
provider's prompt cache has expired, so the old compactable tool results are cleared before
the request rather than re-sent. Upstream reads the config from GrowthBook
(`tengu_slate_heron`); without GrowthBook the defaults stand, and they are **disabled**, so
in this build microcompaction is a no-op unless a caller passes an enabled config.

The trigger needs the last assistant message's time, read from
`additional_kwargs["timestamp"]` (ISO 8601); a message without one never triggers.

Not ported: the cached-microcompact path (`feature('CACHED_MICROCOMPACT')`, API cache
edits), and the compact-warning suppression and cache-break notifications (UI/telemetry).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.loop.context.tokens import MEDIA_BLOCK_TOKENS, rough_token_count_estimation
from app.loop.messages import Message

logger = logging.getLogger(__name__)

#: `TIME_BASED_MC_CLEARED_MESSAGE`.
TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"

#: `COMPACTABLE_TOOLS`, under this build's tool names (`Read` is `ReadFile`, `Write` is
#: `WriteFile`; `SHELL_TOOL_NAMES` is `Bash` and `PowerShell`).
COMPACTABLE_TOOLS: frozenset[str] = frozenset(
    {"ReadFile", "Bash", "PowerShell", "Grep", "Glob", "WebSearch", "WebFetch", "Edit", "WriteFile"}
)


@dataclass(frozen=True, slots=True)
class TimeBasedMCConfig:
    """`TimeBasedMCConfig`."""

    enabled: bool
    gap_threshold_minutes: float
    keep_recent: int


#: `TIME_BASED_MC_CONFIG_DEFAULTS`.
TIME_BASED_MC_CONFIG_DEFAULTS = TimeBasedMCConfig(enabled=False, gap_threshold_minutes=60, keep_recent=5)


def get_time_based_mc_config() -> TimeBasedMCConfig:
    """`getTimeBasedMCConfig`: GrowthBook's `tengu_slate_heron`, whose default is the build's."""
    return TIME_BASED_MC_CONFIG_DEFAULTS


@dataclass(frozen=True, slots=True)
class MicrocompactResult:
    """`MicrocompactResult` (minus `compactionInfo`, which only the cached path fills)."""

    messages: Sequence[Message]
    tokens_saved: int = 0


def collect_compactable_tool_ids(messages: Sequence[Message]) -> list[str]:
    """`collectCompactableToolIds`: compactable tool_use ids, in encounter order."""
    ids: list[str] = []
    for message in messages:
        if getattr(message, "type", None) != "ai":
            continue
        for call in getattr(message, "tool_calls", None) or ():
            if call.get("name") in COMPACTABLE_TOOLS:
                ids.append(str(call.get("id")))
    return ids


def _timestamp(message: Message) -> float:
    kwargs = getattr(message, "additional_kwargs", None)
    raw = kwargs.get("timestamp") if isinstance(kwargs, dict) else None
    if not isinstance(raw, str):
        return math.nan
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return math.nan


def evaluate_time_based_trigger(
    messages: Sequence[Message],
    *,
    is_main_thread: bool,
    now: float | None = None,
    config: TimeBasedMCConfig | None = None,
) -> tuple[float, TimeBasedMCConfig] | None:
    """`evaluateTimeBasedTrigger` -> `(gap_minutes, config)`, or `None` when it does not fire."""
    config = config if config is not None else get_time_based_mc_config()
    if not config.enabled or not is_main_thread:
        return None
    last = next((m for m in reversed(messages) if getattr(m, "type", None) == "ai"), None)
    if last is None:
        return None
    current = now if now is not None else time.time()
    gap_minutes = (current - _timestamp(last)) / 60
    if not math.isfinite(gap_minutes) or gap_minutes < config.gap_threshold_minutes:
        return None
    return gap_minutes, config


def calculate_tool_result_tokens(content: Any) -> int:
    """`calculateToolResultTokens`: text by estimate, media at the fixed size."""
    if not content:
        return 0
    if isinstance(content, str):
        return rough_token_count_estimation(content)
    total = 0
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            total += rough_token_count_estimation(str(item.get("text", "")))
        elif item.get("type") in ("image", "image_url", "document"):
            total += MEDIA_BLOCK_TOKENS
    return total


def _maybe_time_based_microcompact(
    messages: Sequence[Message], *, is_main_thread: bool, now: float | None, config: TimeBasedMCConfig | None
) -> MicrocompactResult | None:
    """`maybeTimeBasedMicrocompact`."""
    trigger = evaluate_time_based_trigger(messages, is_main_thread=is_main_thread, now=now, config=config)
    if trigger is None:
        return None
    gap_minutes, cfg = trigger
    compactable = collect_compactable_tool_ids(messages)
    keep = set(compactable[-max(1, cfg.keep_recent):])
    clear = {tool_id for tool_id in compactable if tool_id not in keep}
    if not clear:
        return None

    tokens_saved = 0
    out: list[Message] = []
    for message in messages:
        if (
            getattr(message, "type", None) == "tool"
            and str(getattr(message, "tool_call_id", "")) in clear
            and message.content != TIME_BASED_MC_CLEARED_MESSAGE
        ):
            tokens_saved += calculate_tool_result_tokens(message.content)
            out.append(message.model_copy(update={"content": TIME_BASED_MC_CLEARED_MESSAGE}))
        else:
            out.append(message)
    if tokens_saved == 0:
        return None
    logger.debug(
        "[TIME-BASED MC] gap %dmin > %smin, cleared %d tool results (~%d tokens), kept last %d",
        round(gap_minutes), cfg.gap_threshold_minutes, len(clear), tokens_saved, len(keep),
    )
    return MicrocompactResult(messages=out, tokens_saved=tokens_saved)


def microcompact_messages(
    messages: Sequence[Message],
    *,
    is_main_thread: bool,
    now: float | None = None,
    config: TimeBasedMCConfig | None = None,
) -> MicrocompactResult:
    """`microcompactMessages`: the time-based path, else the messages unchanged."""
    result = _maybe_time_based_microcompact(messages, is_main_thread=is_main_thread, now=now, config=config)
    return result if result is not None else MicrocompactResult(messages=messages)


__all__ = [
    "COMPACTABLE_TOOLS",
    "TIME_BASED_MC_CLEARED_MESSAGE",
    "TIME_BASED_MC_CONFIG_DEFAULTS",
    "MicrocompactResult",
    "TimeBasedMCConfig",
    "calculate_tool_result_tokens",
    "collect_compactable_tool_ids",
    "evaluate_time_based_trigger",
    "get_time_based_mc_config",
    "microcompact_messages",
]
