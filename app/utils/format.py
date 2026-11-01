"""
Human-readable durations.

Ported from `reference/claude-code/src/utils/format.ts` -> `formatDuration`, without the
`hideTrailingZeros` / `mostSignificantOnly` options, which no ported caller passes.
"""

from __future__ import annotations

import math


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
