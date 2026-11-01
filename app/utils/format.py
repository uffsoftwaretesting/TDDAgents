"""
Human-readable durations.

Ported from `reference/claude-code/src/utils/format.ts` -> `formatDuration`, without the
`hideTrailingZeros` / `mostSignificantOnly` options, which no ported caller passes; and
`formatNumber`, `formatTokens` (`/context`).
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal


def format_duration(ms: float) -> str:
    """`formatDuration(ms)`: `0s`, `0.5s`, `42s`, `2m 0s`, `1h 2m 3s`, `1d 2h 3m`."""
    if ms < 60_000:
        if ms == 0:
            return "0s"
        if ms < 1:
            return f"{ms / 1000:.1f}s"
        return f"{math.floor(ms / 1000)}s"
    days = math.floor(ms / 86_400_000)
    hours = math.floor((ms % 86_400_000) / 3_600_000)
    minutes = math.floor((ms % 3_600_000) / 60_000)
    seconds = round((ms % 60_000) / 1000)
    if seconds == 60:
        seconds, minutes = 0, minutes + 1
    if minutes == 60:
        minutes, hours = 0, hours + 1
    if hours == 24:
        hours, days = 0, days + 1
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    return f"{minutes}m {seconds}s"


#: `Intl.NumberFormat('en-US', {notation: 'compact'})`'s short scales, largest first.
_COMPACT_SCALES: tuple[tuple[int, str], ...] = ((10**12, "T"), (10**9, "B"), (10**6, "M"), (10**3, "K"))
_TENTH = Decimal("0.1")


def _compact_parts(number: float) -> tuple[Decimal, str]:
    """
    The scaled value, rounded to one decimal (ICU's `halfExpand`), and its suffix. A value that
    rounds up to 1000 of one scale moves to the next (`999,950` is `1.0M`, not `1000.0K`).
    """
    exact = Decimal(number)
    scales = [(1, ""), *reversed(_COMPACT_SCALES)]
    index = max(i for i, (size, _) in enumerate(scales) if i == 0 or abs(exact) >= size)
    while True:
        size, suffix = scales[index]
        rounded = (exact / size).quantize(_TENTH, rounding=ROUND_HALF_UP)
        if abs(rounded) < 1000 or index == len(scales) - 1:
            return rounded, suffix
        index += 1


def format_number(number: float) -> str:
    """
    `formatNumber`: compact notation, lowercased (`1321` is `1.3k`, `900` is `900`). From 1000 on
    the one decimal is always shown (`1.0k`), below it only when there is one.
    """
    rounded, suffix = _compact_parts(number)
    text = f"{rounded:.1f}"
    if number < 1000 and text.endswith(".0"):
        text = text[:-2]
    return (text + suffix).lower()


def format_tokens(count: float) -> str:
    """`formatTokens`: `formatNumber` without its first `.0` (`1.0k` is `1k`)."""
    return format_number(count).replace(".0", "", 1)
