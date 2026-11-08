"""
An edit to `.tddagents/project.json` must leave it valid, when it was valid before (2026-11-08).

Ported from `reference/claude-code/src/utils/settings/validateEditTool.ts` ->
`validateInputForSettingsFileEdit` (the settings counterpart is
`app/utils/settings/validate_edit_tool.py`), which `FileEditTool.validateInput` calls last: when the
file passes its schema before the edit, the content after the edit must pass it too, or the edit is
refused with error code 10 and the issues. A file already invalid before the edit may be edited freely:
the edit may be the fix. As upstream, only the Edit tool checks (FileWriteTool has no such step; user
decision 2026-11-08).

Here the schema is the project configuration's (`app/tdd/project_config.py::parse_project_config`).
An edit that passes is read by the session at once (`app/tdd/project_config_watcher.py`).
"""

from __future__ import annotations

import os
from collections.abc import Callable

from app.tdd.project_config import PROJECT_CONFIG_FILE, parse_project_config_text, project_relative_path
from app.tool import ValidationResult

#: Upstream's `errorCode: 10`, as the settings check's.
PROJECT_CONFIG_VALIDATION_ERROR_CODE = 10


def is_project_config_path(file_path: str, root: str | os.PathLike[str] | None) -> bool:
    """Whether `file_path` is the project's `.tddagents/project.json`."""
    return project_relative_path(file_path, root) == PROJECT_CONFIG_FILE


def validate_input_for_project_config_edit(
    file_path: str,
    root: str | os.PathLike[str] | None,
    original_content: str,
    get_updated_content: Callable[[], str],
) -> ValidationResult | None:
    """`validateInputForSettingsFileEdit` for `project.json`: `None` to let the edit through, else the refusal."""
    if not is_project_config_path(file_path, root):
        return None
    before, _ = parse_project_config_text(original_content)
    if before is None:
        return None
    after, issues = parse_project_config_text(get_updated_content())
    if after is not None:
        return None
    listed = "\n".join(f"- {issue}" for issue in issues)
    return ValidationResult(
        valid=False,
        message=f"{PROJECT_CONFIG_FILE} validation failed after edit:\n{listed}",
        error_code=PROJECT_CONFIG_VALIDATION_ERROR_CODE,
    )


__all__ = [
    "PROJECT_CONFIG_VALIDATION_ERROR_CODE",
    "is_project_config_path",
    "validate_input_for_project_config_edit",
]
