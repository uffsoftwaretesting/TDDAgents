"""
An edit to a settings file must leave it valid, when it was valid before.

Ported from `reference/claude-code/src/utils/settings/validateEditTool.ts` ->
`validateInputForSettingsFileEdit`, which `FileEditTool.validateInput` calls last: for a settings file
(`isClaudeSettingsPath`, here `app/utils/permissions/filesystem.py` -> `is_tddagents_settings_path`)
whose content passes `validateSettingsFileContent` (`SettingsSchema().strict()`), the content after the
edit must pass it too, or the edit is refused (error code 10) with the issues and the full schema. A
file already invalid before the edit can be edited freely: the edit may be the fix.

*Divergences:* "Claude Code settings.json" is "TDDAgents settings.json" (the product substitution), and
the strict check knows only the keys this build reads (`SUPPORTED_SETTINGS_KEYS`): a key upstream's
schema declares but this build does not read (`env`, `model`, ...) is an unrecognized field here.
"""

from __future__ import annotations

from collections.abc import Callable

from app.tool import ValidationResult
from app.utils.permissions.filesystem import is_tddagents_settings_path
from app.utils.settings.settings import SettingsLocations
from app.utils.settings.validation import validate_settings_file_content

#: `errorCode: 10`.
SETTINGS_VALIDATION_ERROR_CODE = 10


def validate_input_for_settings_file_edit(
    file_path: str,
    cwd: str,
    locations: SettingsLocations | None,
    original_content: str,
    get_updated_content: Callable[[], str],
) -> ValidationResult | None:
    """`validateInputForSettingsFileEdit`: `None` to let the edit through, else the refusal."""
    if not is_tddagents_settings_path(file_path, cwd, locations):
        return None
    if not validate_settings_file_content(original_content).is_valid:
        return None
    after = validate_settings_file_content(get_updated_content())
    if after.is_valid:
        return None
    return ValidationResult(
        valid=False,
        message=(f"TDDAgents settings.json validation failed after edit:\n{after.error}\n\nFull schema:\n"
                 f"{after.full_schema}\nIMPORTANT: Do not update the env unless explicitly instructed to do so."),
        error_code=SETTINGS_VALIDATION_ERROR_CODE,
    )


__all__ = ["SETTINGS_VALIDATION_ERROR_CODE", "validate_input_for_settings_file_edit"]
