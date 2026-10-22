"""
Auto-mode denial tracking.

Ported from `reference/claude-code/src/utils/permissions/denialTracking.ts` ->
`DenialTrackingState`, `DENIAL_LIMITS`, `createDenialTrackingState`, `recordDenial`,
`recordSuccess`, `shouldFallbackToPrompting`.

`DENIAL_LIMITS` is upstream's own ceiling, ported with its constants (CLAUDE.md: "ceilings
follow claude-code"): after 3 consecutive or 20 total classifier denials an interactive
session falls back to prompting, and a headless one aborts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class DenialTrackingState:
    """`DenialTrackingState`."""

    consecutive_denials: int = 0
    total_denials: int = 0


#: `DENIAL_LIMITS.maxConsecutive`.
MAX_CONSECUTIVE_DENIALS = 3
#: `DENIAL_LIMITS.maxTotal`.
MAX_TOTAL_DENIALS = 20


def create_denial_tracking_state() -> DenialTrackingState:
    """`createDenialTrackingState`."""
    return DenialTrackingState()


def record_denial(state: DenialTrackingState) -> DenialTrackingState:
    """`recordDenial`."""
    return replace(
        state,
        consecutive_denials=state.consecutive_denials + 1,
        total_denials=state.total_denials + 1,
    )


def record_success(state: DenialTrackingState) -> DenialTrackingState:
    """`recordSuccess`: resets the streak, and returns the same state when there is none."""
    if state.consecutive_denials == 0:
        return state
    return replace(state, consecutive_denials=0)


def should_fallback_to_prompting(state: DenialTrackingState) -> bool:
    """`shouldFallbackToPrompting`."""
    return state.consecutive_denials >= MAX_CONSECUTIVE_DENIALS or state.total_denials >= MAX_TOTAL_DENIALS


__all__ = [
    "MAX_CONSECUTIVE_DENIALS",
    "MAX_TOTAL_DENIALS",
    "DenialTrackingState",
    "create_denial_tracking_state",
    "record_denial",
    "record_success",
    "should_fallback_to_prompting",
]
