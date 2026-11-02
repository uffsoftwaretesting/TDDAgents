"""
Plugin policy checks backed by managed settings (policySettings).

Ported from `reference/claude-code/src/utils/plugins/pluginPolicy.ts` -> `isPluginBlockedByPolicy`.

A leaf module (it imports only settings), as upstream keeps it, to avoid import cycles.
`locations` names the run's settings sources (upstream reads its process-wide settings).
"""

from __future__ import annotations

from app.types.permissions import PermissionRuleSource
from app.utils.settings.settings import SettingsLocations, get_settings_for_source


def is_plugin_blocked_by_policy(plugin_id: str, locations: SettingsLocations) -> bool:
    """`isPluginBlockedByPolicy`: policy settings set `enabledPlugins[plugin_id]` to false."""
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations) or {}
    enabled = policy.get("enabledPlugins")
    return isinstance(enabled, dict) and enabled.get(plugin_id) is False


__all__ = ["is_plugin_blocked_by_policy"]
