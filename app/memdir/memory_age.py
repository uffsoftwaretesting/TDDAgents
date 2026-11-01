"""
How old a memory is, said in words a model reasons about.

Ported from `reference/claude-code/src/memdir/memoryAge.ts` -> `memoryAgeDays`, `memoryAge`,
`memoryFreshnessText`, `memoryFreshnessNote`. `now_ms` defaults to the clock (upstream's
`Date.now()`); tests pass it.
"""

from __future__ import annotations

import math
import time

#: Milliseconds in a day.
DAY_MS = 86_400_000


def memory_age_days(mtime_ms: float, now_ms: float | None = None) -> int:
    """Whole days since `mtime_ms`, floored; a future mtime (clock skew) is 0."""
    now = time.time() * 1000 if now_ms is None else now_ms
    return max(0, math.floor((now - mtime_ms) / DAY_MS))


def memory_age(mtime_ms: float, now_ms: float | None = None) -> str:
    """`today`, `yesterday`, or `N days ago`."""
    days = memory_age_days(mtime_ms, now_ms)
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def memory_freshness_text(mtime_ms: float, now_ms: float | None = None) -> str:
    """The staleness caveat for a memory over a day old; `""` for a fresh one."""
    days = memory_age_days(mtime_ms, now_ms)
    if days <= 1:
        return ""
    return (
        f"This memory is {days} days old. "
        "Memories are point-in-time observations, not live state — "
        "claims about code behavior or file:line citations may be outdated. "
        "Verify against current code before asserting as fact."
    )


def memory_freshness_note(mtime_ms: float, now_ms: float | None = None) -> str:
    """The caveat in `<system-reminder>` tags, for a caller that does not wrap it (ReadFile)."""
    text = memory_freshness_text(mtime_ms, now_ms)
    return f"<system-reminder>{text}</system-reminder>\n" if text else ""
