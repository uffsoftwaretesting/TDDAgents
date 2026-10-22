"""
When the loop compacts on its own.

Ported from `reference/claude-code/src/services/compact/autoCompact.ts` ->
`AUTOCOMPACT_BUFFER_TOKENS`, `WARNING_THRESHOLD_BUFFER_TOKENS`,
`ERROR_THRESHOLD_BUFFER_TOKENS`, `MANUAL_COMPACT_BUFFER_TOKENS`,
`MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES`, `getAutoCompactThreshold`,
`calculateTokenWarningState`, `isAutoCompactEnabled`, `shouldAutoCompact`,
`autoCompactIfNeeded`.

Window arithmetic is upstream's: the effective window (`get_effective_context_window_size`,
the model window less the summary reservation) minus a 13k buffer is the autocompact
threshold; 3k under the effective window is the blocking limit. Environment overrides keep
upstream's meaning under `TDDAGENTS_*` names (`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` ->
`TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE`, `CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE` ->
`TDDAGENTS_BLOCKING_LIMIT_OVERRIDE`, `DISABLE_COMPACT` / `DISABLE_AUTO_COMPACT` ->
`TDDAGENTS_DISABLE_COMPACT` / `TDDAGENTS_DISABLE_AUTO_COMPACT`). Upstream's global-config
`autoCompactEnabled` defaults to true and has no equivalent store here, so it stays true.

`MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES` is a ceiling upstream has (a circuit breaker so a
summary request that keeps failing is not retried every turn) and is ported with its
constant, per CLAUDE.md's "ceilings follow claude-code".

Not ported: session-memory compaction (both its GrowthBook flags default false in the
external build), the reactive-only and context-collapse suppressions (feature-gated off),
`RecompactionInfo` (analytics only) and the post-compaction markers.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.loop.compact.compact import (
    CompactionResult,
    CompactSummarizer,
    PostCompactAttachments,
    compact_conversation,
)
from app.loop.compact.post_compact_cleanup import run_post_compact_cleanup
from app.loop.context.tokens import (
    get_effective_context_window_size,
    token_count_with_estimation,
)
from app.loop.messages import Message

logger = logging.getLogger("TDDOrchestrator.Compact")

AUTOCOMPACT_BUFFER_TOKENS = 13_000
WARNING_THRESHOLD_BUFFER_TOKENS = 20_000
ERROR_THRESHOLD_BUFFER_TOKENS = 20_000
MANUAL_COMPACT_BUFFER_TOKENS = 3_000
#: Upstream's circuit breaker: stop attempting autocompact after this many failures in a row.
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3


def _env_truthy(name: str) -> bool:
    value = os.environ.get(name)
    return value is not None and value.strip().lower() in ("1", "true", "yes", "on")


def _parse_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def get_auto_compact_threshold(model: str) -> int:
    """`getAutoCompactThreshold`."""
    effective = get_effective_context_window_size(model)
    threshold = effective - AUTOCOMPACT_BUFFER_TOKENS
    raw = os.environ.get("TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE")
    if raw:
        percent = _parse_float(raw)
        if percent is not None and 0 < percent <= 100:
            return min(math.floor(effective * (percent / 100)), threshold)
    return threshold


def is_auto_compact_enabled() -> bool:
    """`isAutoCompactEnabled`."""
    return not (_env_truthy("TDDAGENTS_DISABLE_COMPACT") or _env_truthy("TDDAGENTS_DISABLE_AUTO_COMPACT"))


@dataclass(frozen=True, slots=True)
class TokenWarningState:
    """`calculateTokenWarningState`'s result."""

    percent_left: int
    is_above_warning_threshold: bool
    is_above_error_threshold: bool
    is_above_auto_compact_threshold: bool
    is_at_blocking_limit: bool


def _js_round(value: float) -> int:
    return math.floor(value + 0.5)


def _blocking_limit_override() -> int | None:
    raw = os.environ.get("TDDAGENTS_BLOCKING_LIMIT_OVERRIDE")
    if not raw:
        return None
    try:
        parsed = int(raw.strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def calculate_token_warning_state(token_usage: int, model: str) -> TokenWarningState:
    """`calculateTokenWarningState`."""
    enabled = is_auto_compact_enabled()
    auto_threshold = get_auto_compact_threshold(model)
    effective = get_effective_context_window_size(model)
    threshold = auto_threshold if enabled else effective
    percent_left = max(0, _js_round(((threshold - token_usage) / threshold) * 100))
    override = _blocking_limit_override()
    blocking_limit = override if override is not None else effective - MANUAL_COMPACT_BUFFER_TOKENS
    return TokenWarningState(
        percent_left=percent_left,
        is_above_warning_threshold=token_usage >= threshold - WARNING_THRESHOLD_BUFFER_TOKENS,
        is_above_error_threshold=token_usage >= threshold - ERROR_THRESHOLD_BUFFER_TOKENS,
        is_above_auto_compact_threshold=enabled and token_usage >= auto_threshold,
        is_at_blocking_limit=token_usage >= blocking_limit,
    )


def should_auto_compact(
    messages: Sequence[Message], model: str, query_source: str | None = None, snip_tokens_freed: int = 0
) -> bool:
    """`shouldAutoCompact`: never inside the compaction or session-memory side calls."""
    if query_source in ("session_memory", "compact"):
        return False
    if not is_auto_compact_enabled():
        return False
    tokens = token_count_with_estimation(messages) - snip_tokens_freed
    return calculate_token_warning_state(tokens, model).is_above_auto_compact_threshold


@dataclass(frozen=True, slots=True)
class AutoCompactOutcome:
    """`autoCompactIfNeeded`'s `{wasCompacted, compactionResult?, consecutiveFailures?}`."""

    was_compacted: bool
    compaction_result: CompactionResult | None = None
    consecutive_failures: int | None = None


async def auto_compact_if_needed(
    messages: Sequence[Message],
    context: Any,
    *,
    model: str,
    tracking: Any,
    summarizer: CompactSummarizer,
    post_compact_attachments: PostCompactAttachments,
    query_source: str | None = None,
    snip_tokens_freed: int = 0,
    transcript_path: str | None = None,
) -> AutoCompactOutcome:
    """`autoCompactIfNeeded`: compact when over the threshold, behind the circuit breaker."""
    if _env_truthy("TDDAGENTS_DISABLE_COMPACT"):
        return AutoCompactOutcome(False)
    failures = getattr(tracking, "consecutive_failures", None)
    if failures is not None and failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
        return AutoCompactOutcome(False)
    if not should_auto_compact(messages, model, query_source, snip_tokens_freed):
        return AutoCompactOutcome(False)
    try:
        result = await compact_conversation(
            messages,
            context,
            summarizer=summarizer,
            trigger="auto",
            post_compact_attachments=post_compact_attachments,
            model=model,
            custom_instructions=None,
            suppress_follow_up_questions=True,
            transcript_path=transcript_path,
        )
    except Exception:
        logger.exception("Autocompact failed")
        next_failures = (failures or 0) + 1
        if next_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
            logger.warning(
                "autocompact: circuit breaker tripped after %d consecutive failures — skipping future attempts",
                next_failures,
            )
        return AutoCompactOutcome(False, None, next_failures)
    run_post_compact_cleanup(context, query_source=query_source)
    return AutoCompactOutcome(True, result, 0)


__all__ = [
    "AUTOCOMPACT_BUFFER_TOKENS",
    "ERROR_THRESHOLD_BUFFER_TOKENS",
    "MANUAL_COMPACT_BUFFER_TOKENS",
    "MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES",
    "WARNING_THRESHOLD_BUFFER_TOKENS",
    "AutoCompactOutcome",
    "TokenWarningState",
    "auto_compact_if_needed",
    "calculate_token_warning_state",
    "get_auto_compact_threshold",
    "is_auto_compact_enabled",
    "should_auto_compact",
]
