"""
The notice that the settings have validation errors, shown when the REPL opens and after every change.

Ported from `reference/claude-code/src/hooks/notifs/useSettingsErrors.tsx` -> `useSettingsErrors`: the
errors are `getSettingsWithAllErrors()`' (the settings files' and the MCP configs'), read when the REPL
mounts and again on every settings change (`useSettingsChange`, here the run's settings change
detector); while there are any, the notice "Found N settings issue(s)" is shown. The REPL has no
notification area (Ink's `addNotification`, a high-priority 60 s warning toast, removed when the errors
are gone), so the notice is written as a line, as the REPL's other notices are, each time the errors
are read and there are some. Remote mode does not exist here.

*Divergence:* upstream's notice ends "· /doctor for details"; `/doctor` is not ported, so the pointer
is left out.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from app.utils.settings.all_errors import get_settings_with_all_errors
from app.utils.settings.settings import SettingsLocations
from app.utils.settings.validation import ValidationError

#: `SETTINGS_ERRORS_NOTIFICATION_KEY`.
SETTINGS_ERRORS_NOTIFICATION_KEY = "settings-errors"


def settings_errors_notification_text(errors: Sequence[ValidationError]) -> str:
    """The notice for `errors` (non-empty)."""
    return f"Found {len(errors)} settings {'issue' if len(errors) == 1 else 'issues'}"


def use_settings_errors(
    write: Callable[[str], None], locations: SettingsLocations | None, detector: Any = None,
) -> Callable[[], None]:
    """Write the notice now if the settings have errors, and after each change that leaves some; unsubscribe."""
    if locations is None:
        return lambda: None
    run_locations = locations

    def show() -> None:
        errors = get_settings_with_all_errors(run_locations)
        if errors:
            write(settings_errors_notification_text(errors))

    show()
    if detector is None:
        return lambda: None
    unsubscribe: Callable[[], None] = detector.subscribe(lambda _source: show())
    return unsubscribe


__all__ = ["SETTINGS_ERRORS_NOTIFICATION_KEY", "settings_errors_notification_text", "use_settings_errors"]
