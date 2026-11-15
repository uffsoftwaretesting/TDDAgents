"""
The notice that the settings have validation errors, shown when the REPL opens and after every change.

Ported from `reference/claude-code/src/hooks/notifs/useSettingsErrors.tsx` -> `useSettingsErrors`: the
errors are `getSettingsWithAllErrors()`' (the settings files' and the MCP configs'), read when the REPL
mounts and again on every settings change (`useSettingsChange`, here the run's settings change
detector); while there are any, the notice "Found N settings issue(s) · /doctor for details" is shown
(`app/screens/doctor.py` lists them). The REPL has no notification area (Ink's `addNotification`, a
high-priority 60 s warning toast), so the notice is written as a line, as the REPL's other notices are,
with the toast's lifetime kept (2026-11-14): `addNotification` skips a notification whose key is
already displayed or queued (`context/notifications.tsx`), so the notice is written only when none is
on screen, that is when it was never shown, `SETTINGS_ERRORS_TIMEOUT_MS` passed since it was, or the
errors went away in between (`removeNotification`). Remote mode does not exist here.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from app.utils.settings.all_errors import get_settings_with_all_errors
from app.utils.settings.settings import SettingsLocations
from app.utils.settings.validation import ValidationError

#: `SETTINGS_ERRORS_NOTIFICATION_KEY`.
SETTINGS_ERRORS_NOTIFICATION_KEY = "settings-errors"
#: The notice's `timeoutMs`: how long it stays on screen, so how long a repeat is skipped.
SETTINGS_ERRORS_TIMEOUT_MS = 60_000


def settings_errors_notification_text(errors: Sequence[ValidationError]) -> str:
    """The notice for `errors` (non-empty)."""
    return f"Found {len(errors)} settings {'issue' if len(errors) == 1 else 'issues'} · /doctor for details"


def use_settings_errors(
    write: Callable[[str], None], locations: SettingsLocations | None, detector: Any = None,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], None]:
    """
    Write the notice now if the settings have errors, and after a change that leaves some while no notice
    is on screen; return the unsubscribe.
    """
    if locations is None:
        return lambda: None
    run_locations = locations
    #: When the notice on screen was written; `None` while none is (never shown, timed out, or removed).
    shown_at: list[float | None] = [None]

    def show() -> None:
        errors = get_settings_with_all_errors(run_locations)
        now = clock()
        if not errors:
            shown_at[0] = None  # `removeNotification(SETTINGS_ERRORS_NOTIFICATION_KEY)`
            return
        last = shown_at[0]
        if last is not None and (now - last) * 1000 < SETTINGS_ERRORS_TIMEOUT_MS:
            return  # the same key is still displayed
        shown_at[0] = now
        write(settings_errors_notification_text(errors))

    show()
    if detector is None:
        return lambda: None
    unsubscribe: Callable[[], None] = detector.subscribe(lambda _source: show())
    return unsubscribe


__all__ = [
    "SETTINGS_ERRORS_NOTIFICATION_KEY",
    "SETTINGS_ERRORS_TIMEOUT_MS",
    "settings_errors_notification_text",
    "use_settings_errors",
]
