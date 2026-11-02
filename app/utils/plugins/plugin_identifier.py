"""
Plugin identifiers (`name@marketplace`) and the scope <-> settings-source mapping.

Ported from `reference/claude-code/src/utils/plugins/pluginIdentifier.ts` -> `ExtendedPluginScope`,
`PersistablePluginScope`, `SETTING_SOURCE_TO_SCOPE`, `ParsedPluginIdentifier`, `parsePluginIdentifier`,
`buildPluginId`, `isOfficialMarketplaceName`, `SCOPE_TO_EDITABLE_SOURCE`, `scopeToSettingSource`,
`settingSourceToScope`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from app.types.permissions import PermissionRuleSource
from app.utils.plugins.schemas import ALLOWED_OFFICIAL_MARKETPLACE_NAMES, PluginScope

#: `ExtendedPluginScope`: the persisted scopes plus the session-only `flag`.
ExtendedPluginScope: TypeAlias = Literal["managed", "user", "project", "local", "flag"]
#: `PersistablePluginScope`.
PersistablePluginScope: TypeAlias = PluginScope
#: The installable scopes (`Exclude<PluginScope, 'managed'>`).
InstallableScope: TypeAlias = Literal["user", "project", "local"]

#: `SETTING_SOURCE_TO_SCOPE`.
SETTING_SOURCE_TO_SCOPE: Mapping[PermissionRuleSource, ExtendedPluginScope] = {
    PermissionRuleSource.POLICY_SETTINGS: "managed",
    PermissionRuleSource.USER_SETTINGS: "user",
    PermissionRuleSource.PROJECT_SETTINGS: "project",
    PermissionRuleSource.LOCAL_SETTINGS: "local",
    PermissionRuleSource.FLAG_SETTINGS: "flag",
}

#: `SCOPE_TO_EDITABLE_SOURCE`.
SCOPE_TO_EDITABLE_SOURCE: Mapping[InstallableScope, PermissionRuleSource] = {
    "user": PermissionRuleSource.USER_SETTINGS,
    "project": PermissionRuleSource.PROJECT_SETTINGS,
    "local": PermissionRuleSource.LOCAL_SETTINGS,
}

#: `EditableSettingSource`.
EDITABLE_SETTING_SOURCES: tuple[PermissionRuleSource, ...] = (
    PermissionRuleSource.USER_SETTINGS,
    PermissionRuleSource.PROJECT_SETTINGS,
    PermissionRuleSource.LOCAL_SETTINGS,
)


@dataclass(frozen=True)
class ParsedPluginIdentifier:
    """`ParsedPluginIdentifier`."""

    name: str
    marketplace: str | None = None


def parse_plugin_identifier(plugin: str) -> ParsedPluginIdentifier:
    """
    `parsePluginIdentifier`: `name` or `name@marketplace`. Only the first `@` separates;
    anything after a second `@` is ignored (marketplace names never contain `@`).
    """
    if "@" in plugin:
        parts = plugin.split("@")
        return ParsedPluginIdentifier(name=parts[0] or "", marketplace=parts[1])
    return ParsedPluginIdentifier(name=plugin)


def build_plugin_id(name: str, marketplace: str | None = None) -> str:
    """`buildPluginId`."""
    return f"{name}@{marketplace}" if marketplace else name


def is_official_marketplace_name(marketplace: str | None) -> bool:
    """`isOfficialMarketplaceName`."""
    return marketplace is not None and marketplace.lower() in ALLOWED_OFFICIAL_MARKETPLACE_NAMES


def scope_to_setting_source(scope: PluginScope) -> PermissionRuleSource:
    """`scopeToSettingSource`: `managed` cannot be installed to."""
    if scope == "managed":
        raise ValueError("Cannot install plugins to managed scope")
    return SCOPE_TO_EDITABLE_SOURCE[scope]


def setting_source_to_scope(source: PermissionRuleSource) -> InstallableScope:
    """`settingSourceToScope`, for the editable sources."""
    for scope, editable in SCOPE_TO_EDITABLE_SOURCE.items():
        if editable == source:
            return scope
    raise ValueError(f"{source} is not an editable setting source")


__all__ = [
    "EDITABLE_SETTING_SOURCES",
    "SCOPE_TO_EDITABLE_SOURCE",
    "SETTING_SOURCE_TO_SCOPE",
    "ExtendedPluginScope",
    "InstallableScope",
    "ParsedPluginIdentifier",
    "PersistablePluginScope",
    "build_plugin_id",
    "is_official_marketplace_name",
    "parse_plugin_identifier",
    "scope_to_setting_source",
    "setting_source_to_scope",
]
