"""
Plugin settings (`enabledPlugins`, `extraKnownMarketplaces`) from `--add-dir` directories.

Ported from `reference/claude-code/src/utils/plugins/addDirPluginSettings.ts` -> `SETTINGS_FILES`,
`getAddDirEnabledPlugins`, `getAddDirExtraMarketplaces`.

These have the lowest priority: callers spread the standard settings on top. Within a directory
`settings.local.json` wins over `settings.json`; across directories the later one wins. The
directories are `locations.additional_directories` (upstream's `getAdditionalDirectoriesForClaudeMd`),
each read under `.tddagents/` (upstream's `.claude/`).
"""

from __future__ import annotations

import os
from typing import Any

from app.utils.settings.settings import (
    LOCAL_SETTINGS_FILENAME,
    SETTINGS_DIR,
    SETTINGS_FILENAME,
    SettingsLocations,
    parse_settings_file,
)

#: `SETTINGS_FILES`, in application order.
SETTINGS_FILES: tuple[str, ...] = (SETTINGS_FILENAME, LOCAL_SETTINGS_FILENAME)


def _merged_key(locations: SettingsLocations, key: str) -> dict[str, Any]:
    from pathlib import Path

    result: dict[str, Any] = {}
    for directory in locations.additional_directories:
        for file in SETTINGS_FILES:
            settings = parse_settings_file(Path(os.path.join(directory, SETTINGS_DIR, file)))
            value = settings.get(key) if settings else None
            if not value or not isinstance(value, dict):
                continue
            result.update(value)
    return result


def get_add_dir_enabled_plugins(locations: SettingsLocations) -> dict[str, Any]:
    """`getAddDirEnabledPlugins`."""
    return _merged_key(locations, "enabledPlugins")


def get_add_dir_extra_marketplaces(locations: SettingsLocations) -> dict[str, Any]:
    """`getAddDirExtraMarketplaces`."""
    return _merged_key(locations, "extraKnownMarketplaces")


__all__ = ["SETTINGS_FILES", "get_add_dir_enabled_plugins", "get_add_dir_extra_marketplaces"]
