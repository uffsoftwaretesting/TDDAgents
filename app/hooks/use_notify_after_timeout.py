"""
When a waiting question notifies the user.

Ported from `reference/claude-code/src/hooks/useNotifyAfterTimeout.ts` -> `DEFAULT_INTERACTION_THRESHOLD_MS`,
`getTimeSinceLastInteraction`, `hasRecentInteraction`, `shouldNotify`. The hook itself is the REPL's
`_notify_after_timeout` (`app/screens/repl.py`): it stamps the clock when the question shows (upstream's mount)
and checks every threshold (upstream's `setInterval`) until the user has been idle that long, then notifies once.
The clock moves on every keystroke (`app/ink/components/app.py`). Upstream's `NODE_ENV === 'test'` guard is not
ported: the suite drives the timer itself.
"""

from __future__ import annotations

import time

from app.bootstrap.state import get_last_interaction_time

#: `DEFAULT_INTERACTION_THRESHOLD_MS`: 6 s without input.
DEFAULT_INTERACTION_THRESHOLD_MS = 6000


def get_time_since_last_interaction_ms() -> float:
    """`getTimeSinceLastInteraction`."""
    return (time.time() - get_last_interaction_time()) * 1000


def has_recent_interaction(threshold_ms: float) -> bool:
    """`hasRecentInteraction`."""
    return get_time_since_last_interaction_ms() < threshold_ms


def should_notify(threshold_ms: float) -> bool:
    """`shouldNotify`: the user has been idle for the threshold."""
    return not has_recent_interaction(threshold_ms)


__all__ = ["DEFAULT_INTERACTION_THRESHOLD_MS", "get_time_since_last_interaction_ms", "has_recent_interaction",
           "should_notify"]
