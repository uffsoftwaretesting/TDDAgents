"""
The dialog an interactive session shows when its settings files have validation errors.

Ported from `reference/claude-code/src/components/InvalidSettingsDialog.tsx` -> `InvalidSettingsDialog`:
the "Settings Error" dialog listing the errors (`app/components/validation_errors_list.py`), the note
that a file with errors is skipped whole, and the choice to exit and fix them or to continue without
them; Esc is exit. The launcher is `app/interactive_helpers.py` -> `launch_invalid_settings_dialog`.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.components.design_system.dialog import Cancelled, DialogIO, select
from app.components.validation_errors_list import render_validation_errors
from app.utils.settings.validation import ValidationError

TITLE = "Settings Error"
SKIPPED_ENTIRELY = "Files with errors are skipped entirely, not just the invalid settings."
EXIT = "Exit and fix manually"
CONTINUE = "Continue without these settings"


async def invalid_settings_dialog(io: DialogIO, settings_errors: Sequence[ValidationError]) -> bool:
    """The dialog; `True` to continue without the invalid files, `False` to exit (also on Esc)."""
    lines = [TITLE, "", *render_validation_errors(settings_errors), SKIPPED_ENTIRELY, ""]
    picked = await select(io, lines, [EXIT, CONTINUE], "exit")
    return not isinstance(picked, Cancelled) and picked == 1


__all__ = ["CONTINUE", "EXIT", "SKIPPED_ENTIRELY", "TITLE", "invalid_settings_dialog"]
