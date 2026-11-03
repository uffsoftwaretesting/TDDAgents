"""
The settings writes TDDAgents makes itself, so the settings watcher ignores their echoes.

Ported from `reference/claude-code/src/utils/settings/internalWrites.ts` -> `markInternalWrite`,
`consumeInternalWrite`, `clearInternalWrites`.

`update_settings_for_source` (`app/utils/permissions/permission_update.py`) marks a file just
before writing it; the change detector (`app/utils/settings/change_detector.py`) consumes the mark
when it sees the change, so a rule the user saved through a permission dialog does not come back
as an external edit. A mark is consumed once: the next, real, external change is seen. The map is
process-wide, as upstream's module state; callers pass resolved paths.
"""

from __future__ import annotations

import time

_timestamps: dict[str, float] = {}


def mark_internal_write(path: str) -> None:
    """`markInternalWrite`."""
    _timestamps[path] = time.monotonic()


def consume_internal_write(path: str, window_ms: int) -> bool:
    """`consumeInternalWrite`: whether `path` was marked within `window_ms`; a match consumes the mark."""
    ts = _timestamps.get(path)
    if ts is not None and (time.monotonic() - ts) * 1000 < window_ms:
        del _timestamps[path]
        return True
    return False


def clear_internal_writes() -> None:
    """`clearInternalWrites`."""
    _timestamps.clear()


__all__ = ["clear_internal_writes", "consume_internal_write", "mark_internal_write"]
