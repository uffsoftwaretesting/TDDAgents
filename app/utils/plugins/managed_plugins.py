"""
Plugin names locked by org policy.

Ported from `reference/claude-code/src/utils/plugins/managedPlugins.ts` -> `getManagedPluginNames`.
"""

from __future__ import annotations

from app.types.permissions import PermissionRuleSource
from app.utils.settings.settings import SettingsLocations, get_settings_for_source


def get_managed_plugin_names(locations: SettingsLocations) -> set[str] | None:
    """
    `getManagedPluginNames`: the names of the `plugin@marketplace` boolean entries (true or
    false) in policy `enabledPlugins`; `None` when there are none (no policy in effect).
    """
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations) or {}
    enabled = policy.get("enabledPlugins")
    if not enabled or not isinstance(enabled, dict):
        return None
    names: set[str] = set()
    for plugin_id, value in enabled.items():
        if not isinstance(value, bool) or "@" not in plugin_id:
            continue
        name = plugin_id.split("@")[0]
        if name:
            names.add(name)
    return names or None


__all__ = ["get_managed_plugin_names"]
