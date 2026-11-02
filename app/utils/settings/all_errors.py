"""
The settings' errors together with the MCP configs' errors.

Ported from `reference/claude-code/src/utils/settings/allErrors.ts` -> `getSettingsWithAllErrors`:
`getSettingsWithErrors()`' errors plus the user, project and local MCP scopes' errors. It lives
apart from `settings.py` for upstream's reason: the MCP config module imports the settings
module. The settings half is what the lenient reading here records (`get_settings_file_errors`:
a settings file that exists but is ignored). Only the errors are returned, each one line naming
its file: upstream's merged `settings` half has no reader here (`load_settings` gives it).
"""

from __future__ import annotations

from typing import Literal

from app.services.mcp.config import get_mcp_configs_by_scope
from app.utils.settings.settings import SettingsLocations, get_settings_file_errors

#: The MCP scopes whose errors count (`'dynamic'` throws at startup instead, upstream).
MCP_ERROR_SCOPES: tuple[Literal["user", "project", "local"], ...] = ("user", "project", "local")


def get_settings_with_all_errors(locations: SettingsLocations) -> list[str]:
    """`getSettingsWithAllErrors().errors`."""
    errors = [f"{path}: invalid settings file, ignored" for path in get_settings_file_errors(locations)]
    for scope in MCP_ERROR_SCOPES:
        _, mcp_errors = get_mcp_configs_by_scope(scope, locations.project_root, locations)
        errors.extend(f"{error.file or scope}: {error.message}" for error in mcp_errors)
    return errors


__all__ = ["MCP_ERROR_SCOPES", "get_settings_with_all_errors"]
