"""
Which hooks a run may execute, captured once at startup.

Ported from `reference/claude-code/src/utils/hooks/hooksConfigSnapshot.ts` ->
`getHooksFromAllowedSources`, `shouldAllowManagedHooksOnly`,
`shouldDisableAllHooksIncludingManaged`, `captureHooksConfigSnapshot`, and
`src/utils/settings/pluginOnlyPolicy.ts` -> `isRestrictedToPluginOnly`.

The settings sources are the ones the permission context is built from
(`permissions/settings.py`: user, project, local, flag, policy + `managed-settings.d`), so a
run's hooks and its permission rules come from exactly the same files.

Policy decides first, and nothing below it can relax it:

* policy `disableAllHooks` -> no hooks at all;
* policy `allowManagedHooksOnly` (or `strictPluginOnlyCustomization` covering hooks — this
  build has no plugins, so that leaves policy hooks too) -> policy hooks only;
* `disableAllHooks` in any other source -> policy hooks only, because a user-editable
  file cannot switch off what an administrator configured;
* otherwise every source's hooks, merged (arrays concatenate, as upstream's settings merge).

The snapshot is **frozen for the run**, as upstream's is for a session: a hook edited
mid-run does not change what the run executes, so the record of a run can name its hooks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.schemas.hooks import HookMatcher, parse_hooks_settings
from app.types.permissions import PermissionRuleSource
from app.utils.settings.settings import (
    SettingsLocations,
    get_settings_for_source,
    load_settings,
)


def is_restricted_to_plugin_only(policy: Mapping[str, Any] | None, surface: str) -> bool:
    """`isRestrictedToPluginOnly`."""
    value = (policy or {}).get("strictPluginOnlyCustomization")
    if value is True:
        return True
    if isinstance(value, list):
        return surface in value
    return False


def get_hooks_from_allowed_sources(
    policy: Mapping[str, Any] | None, merged: Mapping[str, Any]
) -> Any:
    """`getHooksFromAllowedSources`: the raw `hooks` block the run is allowed to use."""
    policy = policy or {}
    if policy.get("disableAllHooks") is True:
        return {}
    if policy.get("allowManagedHooksOnly") is True:
        return policy.get("hooks") or {}
    if is_restricted_to_plugin_only(policy, "hooks"):
        return policy.get("hooks") or {}
    if merged.get("disableAllHooks") is True:
        return policy.get("hooks") or {}
    return merged.get("hooks") or {}


def should_allow_managed_hooks_only(policy: Mapping[str, Any] | None, merged: Mapping[str, Any]) -> bool:
    """`shouldAllowManagedHooksOnly`."""
    policy = policy or {}
    if policy.get("allowManagedHooksOnly") is True:
        return True
    return merged.get("disableAllHooks") is True and policy.get("disableAllHooks") is not True


def should_disable_all_hooks_including_managed(policy: Mapping[str, Any] | None) -> bool:
    """`shouldDisableAllHooksIncludingManaged`: only policy can switch managed hooks off."""
    return (policy or {}).get("disableAllHooks") is True


@dataclass(frozen=True, slots=True)
class HooksConfigSnapshot:
    """`initialHooksConfig` plus the two policy gates, read once per run."""

    hooks: Mapping[str, tuple[HookMatcher, ...]] = field(default_factory=dict)
    managed_only: bool = False
    disable_all: bool = False

    def matchers_for(self, event: str) -> tuple[HookMatcher, ...]:
        return tuple(self.hooks.get(event, ()))

    def has_hook_for_event(self, event: str) -> bool:
        """`hasHookForEvent`'s snapshot check: any matcher at all for the event."""
        return len(self.hooks.get(event, ())) > 0


def capture_hooks_config_snapshot(locations: SettingsLocations) -> HooksConfigSnapshot:
    """`captureHooksConfigSnapshot`, from the same settings files as the permission rules."""
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations)
    merged = load_settings(locations)
    raw = get_hooks_from_allowed_sources(policy, merged)
    return HooksConfigSnapshot(
        hooks=parse_hooks_settings(raw, origin="settings"),
        managed_only=should_allow_managed_hooks_only(policy, merged),
        disable_all=should_disable_all_hooks_including_managed(policy),
    )


__all__ = [
    "HooksConfigSnapshot",
    "capture_hooks_config_snapshot",
    "get_hooks_from_allowed_sources",
    "is_restricted_to_plugin_only",
    "should_allow_managed_hooks_only",
    "should_disable_all_hooks_including_managed",
]
