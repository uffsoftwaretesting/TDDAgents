"""
The plugin settings base layer of the settings cascade.

Ported from `reference/claude-code/src/utils/settings/settingsCache.ts` -> `getPluginSettingsBase`,
`setPluginSettingsBase`, `clearPluginSettingsBase`.

The plugin loader writes the merged allowlisted settings of the enabled plugins here
(`cachePluginSettings`), and `load_settings` reads them as the lowest-priority base under every
file source. Process-wide, as upstream's module state. Upstream's session settings caches
(`sessionSettingsCache`, `perSourceCache`, `parseFileCache`, `resetSettingsCache`) have no
counterpart: this repository reads the settings files on every `load_settings`, so there is no
cache to reset.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_plugin_settings_base: dict[str, Any] | None = None


def get_plugin_settings_base() -> dict[str, Any] | None:
    """`getPluginSettingsBase`."""
    return _plugin_settings_base


def set_plugin_settings_base(settings: Mapping[str, Any] | None) -> None:
    """`setPluginSettingsBase`."""
    global _plugin_settings_base
    _plugin_settings_base = dict(settings) if settings is not None else None


def clear_plugin_settings_base() -> None:
    """`clearPluginSettingsBase`."""
    global _plugin_settings_base
    _plugin_settings_base = None


__all__ = ["clear_plugin_settings_base", "get_plugin_settings_base", "set_plugin_settings_base"]
