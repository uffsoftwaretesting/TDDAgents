"""
The settings' errors together with the MCP configs' errors.

Ported from `reference/claude-code/src/utils/settings/allErrors.ts` -> `getSettingsWithAllErrors`:
`getSettingsWithErrors()`' errors (`app/utils/settings/settings.py` -> `get_settings_with_errors`) plus
the user, project and local MCP scopes' errors, each with its `mcpErrorMetadata` (`scope`,
`serverName`, `severity`). It lives apart from `settings.py` for upstream's reason: the MCP config
module imports the settings module. Only the errors are returned: upstream's merged `settings` half
has no reader here (`load_settings` gives it).
"""

from __future__ import annotations

from typing import Literal

from app.services.mcp.config import get_mcp_configs_by_scope
from app.utils.settings.settings import SettingsLocations, get_settings_with_errors
from app.utils.settings.validation import ValidationError

#: The MCP scopes whose errors count (`'dynamic'` throws at startup instead, upstream).
MCP_ERROR_SCOPES: tuple[Literal["user", "project", "local"], ...] = ("user", "project", "local")


def get_settings_with_all_errors(locations: SettingsLocations) -> list[ValidationError]:
    """`getSettingsWithAllErrors().errors`."""
    _, errors = get_settings_with_errors(locations)
    all_errors = list(errors)
    for scope in MCP_ERROR_SCOPES:
        _, mcp_errors = get_mcp_configs_by_scope(scope, locations.project_root, locations)
        all_errors.extend(ValidationError(
            file=error.file, path=error.path, message=error.message, suggestion=error.suggestion,
            mcp_error_metadata={"scope": error.scope, "serverName": error.server_name, "severity": error.severity},
        ) for error in mcp_errors)
    return all_errors


def is_mcp_error(error: ValidationError) -> bool:
    """`e.mcpErrorMetadata`: an MCP configuration's error, not a settings file's."""
    return error.mcp_error_metadata is not None


__all__ = ["MCP_ERROR_SCOPES", "get_settings_with_all_errors", "is_mcp_error"]
