"""
The plugin operations (install, uninstall, enable, disable, update) as library functions.

Ported from `reference/claude-code/src/services/plugins/pluginOperations.ts` ->
`VALID_INSTALLABLE_SCOPES`, `VALID_UPDATE_SCOPES`, `assertInstallableScope`,
`isInstallableScope`, `getProjectPathForScope`, `isPluginEnabledAtProjectScope`,
`PluginOperationResult`, `PluginUpdateResult`, `findPluginInSettings`, `findPluginByIdentifier`,
`resolveDelistedPluginId`, `getPluginInstallationFromV2`, `installPluginOp`, `uninstallPluginOp`,
`setPluginEnabledOp`, `enablePluginOp`, `disablePluginOp`, `disableAllPluginsOp`,
`updatePluginOp`, `performPluginUpdate`.

They never print and never exit: each returns a result with its message (the CLI wrappers in
`plugin_cli_commands.py` print it). Every operation takes the run's (or the CLI's)
`SettingsLocations`; upstream's `getOriginalCwd()`, the project path of project and local
installations, is `locations.project_root`.

Divergence: the user-facing hints name this build's command, `python -m app.main plugin …`
(`PLUGIN_CLI`), instead of `claude plugin …`.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from app.constants.product import CLI_COMMAND
from app.plugins.builtin_plugins import is_builtin_plugin_id
from app.types.permissions import PermissionRuleSource
from app.types.plugin import LoadedPlugin
from app.utils.permissions.permission_update import update_settings_for_source
from app.utils.plugins.cache_utils import clear_all_caches, mark_plugin_version_orphaned
from app.utils.plugins.dependency_resolver import (
    find_reverse_dependents,
    format_reverse_dependents_suffix,
)
from app.utils.plugins.installed_plugins_manager import (
    load_installed_plugins_from_disk,
    load_installed_plugins_v2,
    remove_plugin_installation,
    update_installation_path_on_disk,
)
from app.utils.plugins.marketplace_manager import (
    get_marketplace,
    get_plugin_by_id,
    load_known_marketplaces_config,
)
from app.utils.plugins.plugin_directories import (
    PLUGIN_PACKAGE_DIR,
    delete_plugin_data_dir,
)
from app.utils.plugins.plugin_identifier import (
    InstallableScope,
    parse_plugin_identifier,
    scope_to_setting_source,
)
from app.utils.plugins.plugin_installation_helpers import (
    format_resolution_error,
    install_resolved_plugin,
)
from app.utils.plugins.plugin_loader import (
    cache_plugin,
    copy_plugin_to_versioned_cache,
    get_versioned_cache_path,
    get_versioned_zip_cache_path,
    load_all_plugins,
    load_plugin_manifest,
)
from app.utils.plugins.plugin_options_storage import delete_plugin_options
from app.utils.plugins.plugin_policy import is_plugin_blocked_by_policy
from app.utils.plugins.plugin_startup_check import get_plugin_editable_scopes
from app.utils.plugins.plugin_versioning import calculate_plugin_version
from app.utils.plugins.schemas import (
    PluginManifest,
    PluginMarketplaceEntry,
    PluginScope,
)
from app.utils.settings.settings import SettingsLocations, get_settings_for_source
from app.utils.string_utils import plural

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `VALID_INSTALLABLE_SCOPES`: `managed` installs only come from managed settings.
VALID_INSTALLABLE_SCOPES: tuple[InstallableScope, ...] = ("user", "project", "local")
#: `VALID_UPDATE_SCOPES`: managed plugins can be updated.
VALID_UPDATE_SCOPES: tuple[PluginScope, ...] = ("user", "project", "local", "managed")
#: This build's command for the plugin CLI (upstream's `claude plugin`), named in hints.
PLUGIN_CLI = f"{CLI_COMMAND} plugin"
_SCOPE_PRECEDENCE: dict[str, int] = {"user": 0, "project": 1, "local": 2}


def assert_installable_scope(scope: str) -> None:
    """`assertInstallableScope`: `ValueError` unless `scope` is user, project or local."""
    if scope not in VALID_INSTALLABLE_SCOPES:
        raise ValueError(f'Invalid scope "{scope}". Must be one of: {", ".join(VALID_INSTALLABLE_SCOPES)}')


def is_installable_scope(scope: str) -> bool:
    """`isInstallableScope`."""
    return scope in VALID_INSTALLABLE_SCOPES


def get_project_path_for_scope(scope: str, locations: SettingsLocations) -> str | None:
    """`getProjectPathForScope`: the project for project and local scopes."""
    return str(locations.project_root) if scope in ("project", "local") else None


def is_plugin_enabled_at_project_scope(plugin_id: str, locations: SettingsLocations) -> bool:
    """`isPluginEnabledAtProjectScope`: `.tddagents/settings.json` enables it (value `true`)."""
    settings = get_settings_for_source(PermissionRuleSource.PROJECT_SETTINGS, locations) or {}
    enabled = settings.get("enabledPlugins")
    return isinstance(enabled, dict) and enabled.get(plugin_id) is True


@dataclass(frozen=True)
class PluginOperationResult:
    """`PluginOperationResult`."""

    success: bool
    message: str
    plugin_id: str | None = None
    plugin_name: str | None = None
    scope: str | None = None
    #: The plugins declaring this one a dependency (a warning on uninstall / disable).
    reverse_dependents: list[str] | None = None


@dataclass(frozen=True)
class PluginUpdateResult:
    """`PluginUpdateResult`."""

    success: bool
    message: str
    plugin_id: str | None = None
    new_version: str | None = None
    old_version: str | None = None
    already_up_to_date: bool | None = None
    scope: str | None = None


def _enabled_plugins(source: PermissionRuleSource, locations: SettingsLocations) -> dict[str, Any]:
    value = (get_settings_for_source(source, locations) or {}).get("enabledPlugins")
    return dict(value) if isinstance(value, dict) else {}


def find_plugin_in_settings(plugin: str, locations: SettingsLocations) -> tuple[str, InstallableScope] | None:
    """
    `findPluginInSettings`: the most specific editable scope (local > project > user) naming the
    plugin, enabled or not, with its full id. A bare name matches any `<name>@…` key.
    """
    has_marketplace = "@" in plugin
    search_order: tuple[InstallableScope, ...] = ("local", "project", "user")
    for scope in search_order:
        for key in _enabled_plugins(scope_to_setting_source(scope), locations):
            if (key == plugin) if has_marketplace else key.startswith(f"{plugin}@"):
                return key, scope
    return None


def find_plugin_by_identifier(plugin: str, plugins: Sequence[LoadedPlugin]) -> LoadedPlugin | None:
    """`findPluginByIdentifier`: by name, or name and marketplace."""
    parsed = parse_plugin_identifier(plugin)
    # Upstream's second test (same name, marketplace in the source) is implied by the first.
    return next((p for p in plugins if p.name in (plugin, parsed.name)), None)


def resolve_delisted_plugin_id(plugin: str) -> tuple[str, str] | None:
    """`resolveDelistedPluginId`: `(plugin_id, name)` from the installation record, for a delisted plugin."""
    name = parse_plugin_identifier(plugin).name
    installed = load_installed_plugins_v2()["plugins"]
    if installed.get(plugin):
        return plugin, name
    for key, entries in installed.items():
        if parse_plugin_identifier(key).name == name and entries:
            return key, name
    return None


def get_plugin_installation_from_v2(plugin_id: str, locations: SettingsLocations) -> tuple[str, str | None]:
    """
    `getPluginInstallationFromV2`: `(scope, project_path)` of the most relevant installation —
    local (this project) > project (this project) > user > the first one; `user` when none.
    """
    installations = load_installed_plugins_v2()["plugins"].get(plugin_id) or []
    if not installations:
        return "user", None
    current = str(locations.project_root)
    for scope in ("local", "project"):
        found = next((i for i in installations if i["scope"] == scope and i.get("projectPath") == current), None)
        if found is not None:
            return found["scope"], found.get("projectPath")
    if any(i["scope"] == "user" for i in installations):
        return "user", None
    return installations[0]["scope"], installations[0].get("projectPath")


# ── install ───────────────────────────────────────────────────────────────────

async def install_plugin_op(
    plugin: str, scope: InstallableScope = "user", *, locations: SettingsLocations
) -> PluginOperationResult:
    """
    `installPluginOp` (settings first): find the plugin in the materialized marketplaces, write
    the setting, then cache and record it (`installResolvedPlugin`).
    """
    assert_installable_scope(scope)
    parsed = parse_plugin_identifier(plugin)
    found_entry: PluginMarketplaceEntry | None = None
    found_marketplace: str | None = None
    install_location: str | None = None
    if parsed.marketplace:
        info = await get_plugin_by_id(plugin)
        if info is not None:
            found_entry, found_marketplace = info.entry, parsed.marketplace
            install_location = info.marketplace_install_location
    else:
        for name, config in (await load_known_marketplaces_config()).items():
            try:
                marketplace = await get_marketplace(name)
            except Exception as exc:  # noqa: BLE001 - upstream logs a marketplace that fails and goes on
                logger.error("Failed to load marketplace %s: %s", name, exc)
                continue
            entry = next((p for p in marketplace["plugins"] if p["name"] == parsed.name), None)
            if entry is not None:
                found_entry, found_marketplace, install_location = entry, name, config["installLocation"]
                break
    if found_entry is None or found_marketplace is None:
        where = f'marketplace "{parsed.marketplace}"' if parsed.marketplace else "any configured marketplace"
        return PluginOperationResult(False, f'Plugin "{parsed.name}" not found in {where}')
    plugin_id = f"{found_entry['name']}@{found_marketplace}"
    result = await install_resolved_plugin(plugin_id=plugin_id, entry=found_entry, scope=scope, locations=locations,
                                           marketplace_install_location=install_location)
    if not result.ok:
        return PluginOperationResult(False, _install_failure_message(result))
    return PluginOperationResult(
        True, f"Successfully installed plugin: {plugin_id} (scope: {scope}){result.dep_note}",
        plugin_id=plugin_id, plugin_name=found_entry["name"], scope=scope)


def _install_failure_message(result: Any) -> str:
    if result.reason == "local-source-no-location":
        return f'Cannot install local plugin "{result.plugin_name}" without marketplace install location'
    if result.reason == "settings-write-failed":
        return f"Failed to update settings: {result.message}"
    if result.reason == "resolution-failed":
        return format_resolution_error(result.resolution)
    if result.reason == "blocked-by-policy":
        return f'Plugin "{result.plugin_name}" is blocked by your organization\'s policy and cannot be installed'
    return (f'Plugin "{result.plugin_name}" depends on "{result.blocked_dependency}", which is blocked by your '
            "organization's policy")


# ── uninstall ─────────────────────────────────────────────────────────────────

async def uninstall_plugin_op(
    plugin: str, scope: InstallableScope = "user", delete_data_dir: bool = True, *, locations: SettingsLocations
) -> PluginOperationResult:
    """
    `uninstallPluginOp`: remove the setting at `scope`, the scope's installation record, and,
    with the last installation, the cached version (marked orphaned), the stored options and
    (unless kept) the data directory. Plugins depending on it are named, never blocking.
    """
    assert_installable_scope(scope)
    loaded = await load_all_plugins(locations)
    all_plugins = [*loaded.enabled, *loaded.disabled]
    found = find_plugin_by_identifier(plugin, all_plugins)
    setting_source = scope_to_setting_source(scope)
    enabled_plugins = _enabled_plugins(setting_source, locations)
    if found is not None:
        plugin_id = next((k for k in enabled_plugins if k in (plugin, found.name) or k.startswith(f"{found.name}@")),
                         plugin if "@" in plugin else found.name)
        plugin_name = found.name
    else:
        resolved = resolve_delisted_plugin_id(plugin)
        if resolved is None:
            return PluginOperationResult(False, f'Plugin "{plugin}" not found in installed plugins')
        plugin_id, plugin_name = resolved
    project_path = get_project_path_for_scope(scope, locations)
    installations = load_installed_plugins_v2()["plugins"].get(plugin_id) or []
    installation = next((i for i in installations if i["scope"] == scope and i.get("projectPath") == project_path),
                        None)
    if installation is None:
        actual_scope, _ = get_plugin_installation_from_v2(plugin_id, locations)
        if actual_scope != scope and installations:
            if actual_scope == "project":
                return PluginOperationResult(False, (
                    f'Plugin "{plugin}" is enabled at project scope (.tddagents/settings.json, shared with your '
                    f"team). To disable just for you: {PLUGIN_CLI} disable {plugin} --scope local"))
            return PluginOperationResult(False, (
                f'Plugin "{plugin}" is installed in {actual_scope} scope, not {scope}. Use --scope {actual_scope} to '
                "uninstall."))
        return PluginOperationResult(
            False, f'Plugin "{plugin}" is not installed in {scope} scope. Use --scope to specify the correct scope.')
    install_path = installation["installPath"]
    update_settings_for_source(setting_source, locations, {"enabledPlugins": {**enabled_plugins, plugin_id: None}})
    clear_all_caches()
    remove_plugin_installation(plugin_id, scope, project_path)
    is_last_scope = not load_installed_plugins_v2()["plugins"].get(plugin_id)
    if is_last_scope and install_path:
        await mark_plugin_version_orphaned(install_path)
    if is_last_scope:
        delete_plugin_options(plugin_id, locations)
        if delete_data_dir:
            await delete_plugin_data_dir(plugin_id)
    reverse = find_reverse_dependents(plugin_id, all_plugins)
    return PluginOperationResult(
        True, f"Successfully uninstalled plugin: {plugin_name} (scope: {scope})"
              f"{format_reverse_dependents_suffix(reverse)}",
        plugin_id=plugin_id, plugin_name=plugin_name, scope=scope, reverse_dependents=reverse or None)


# ── enable / disable ──────────────────────────────────────────────────────────

async def set_plugin_enabled_op(
    plugin: str, enabled: bool, scope: InstallableScope | None = None, *, locations: SettingsLocations
) -> PluginOperationResult:
    """
    `setPluginEnabledOp` (settings first): resolve the id and scope from settings (an explicit
    scope wins; else the most specific scope naming it; else `user` for a full id), refuse a
    policy-blocked enable, a scope mismatch that is not an override, and a no-op, then write.
    A built-in plugin is always set at user scope.
    """
    operation = "enable" if enabled else "disable"
    if is_builtin_plugin_id(plugin):
        error = update_settings_for_source(PermissionRuleSource.USER_SETTINGS, locations, {
            "enabledPlugins": {**_enabled_plugins(PermissionRuleSource.USER_SETTINGS, locations), plugin: enabled}})
        if error is not None:
            return PluginOperationResult(False, f"Failed to {operation} built-in plugin: {error}")
        clear_all_caches()
        name = parse_plugin_identifier(plugin).name
        return PluginOperationResult(True, f"Successfully {operation}d built-in plugin: {name}", plugin_id=plugin,
                                     plugin_name=name, scope="user")
    if scope is not None:
        assert_installable_scope(scope)
    found = find_plugin_in_settings(plugin, locations)
    resolved_scope: InstallableScope
    if scope is not None:
        resolved_scope = scope
        if found is not None:
            plugin_id = found[0]
        elif "@" in plugin:
            plugin_id = plugin
        else:
            return PluginOperationResult(
                False, f'Plugin "{plugin}" not found in settings. Use plugin@marketplace format.')
    elif found is not None:
        plugin_id, resolved_scope = found
    elif "@" in plugin:
        plugin_id, resolved_scope = plugin, "user"
    else:
        return PluginOperationResult(
            False, f'Plugin "{plugin}" not found in any editable settings scope. Use plugin@marketplace format.')
    if enabled and is_plugin_blocked_by_policy(plugin_id, locations):
        return PluginOperationResult(
            False, f'Plugin "{plugin_id}" is blocked by your organization\'s policy and cannot be enabled')
    setting_source = scope_to_setting_source(resolved_scope)
    scope_value = _enabled_plugins(setting_source, locations).get(plugin_id)
    is_override = scope is not None and found is not None and _SCOPE_PRECEDENCE[scope] > _SCOPE_PRECEDENCE[found[1]]
    if scope is not None and scope_value is None and found is not None and found[1] != scope and not is_override:
        return PluginOperationResult(False, (
            f'Plugin "{plugin}" is installed at {found[1]} scope, not {scope}. Use --scope {found[1]} or omit --scope '
            "to auto-detect."))
    currently_enabled = (scope_value is True if scope is not None and not is_override
                         else plugin_id in get_plugin_editable_scopes(locations))
    if enabled == currently_enabled:
        at = f" at {scope} scope" if scope is not None else ""
        return PluginOperationResult(False, f'Plugin "{plugin}" is already {operation}d{at}')
    reverse: list[str] | None = None
    if not enabled:
        loaded = await load_all_plugins(locations)
        reverse = find_reverse_dependents(plugin_id, [*loaded.enabled, *loaded.disabled]) or None
    error = update_settings_for_source(setting_source, locations, {
        "enabledPlugins": {**_enabled_plugins(setting_source, locations), plugin_id: enabled}})
    if error is not None:
        return PluginOperationResult(False, f"Failed to {operation} plugin: {error}")
    clear_all_caches()
    name = parse_plugin_identifier(plugin_id).name
    return PluginOperationResult(
        True, f"Successfully {operation}d plugin: {name} (scope: {resolved_scope})"
              f"{format_reverse_dependents_suffix(reverse)}",
        plugin_id=plugin_id, plugin_name=name, scope=resolved_scope, reverse_dependents=reverse)


async def enable_plugin_op(
    plugin: str, scope: InstallableScope | None = None, *, locations: SettingsLocations
) -> PluginOperationResult:
    """`enablePluginOp`."""
    return await set_plugin_enabled_op(plugin, True, scope, locations=locations)


async def disable_plugin_op(
    plugin: str, scope: InstallableScope | None = None, *, locations: SettingsLocations
) -> PluginOperationResult:
    """`disablePluginOp`."""
    return await set_plugin_enabled_op(plugin, False, scope, locations=locations)


async def disable_all_plugins_op(*, locations: SettingsLocations) -> PluginOperationResult:
    """`disableAllPluginsOp`: every enabled plugin disabled; the failures listed."""
    enabled_plugins = get_plugin_editable_scopes(locations)
    if not enabled_plugins:
        return PluginOperationResult(True, "No enabled plugins to disable")
    disabled: list[str] = []
    errors: list[str] = []
    for plugin_id in enabled_plugins:
        result = await set_plugin_enabled_op(plugin_id, False, locations=locations)
        if result.success:
            disabled.append(plugin_id)
        else:
            errors.append(f"{plugin_id}: {result.message}")
    if errors:
        return PluginOperationResult(False, (
            f"Disabled {len(disabled)} {plural(len(disabled), 'plugin')}, {len(errors)} failed:\n" + "\n".join(errors)))
    return PluginOperationResult(True, f"Disabled {len(disabled)} {plural(len(disabled), 'plugin')}")


# ── update ────────────────────────────────────────────────────────────────────

async def update_plugin_op(plugin: str, scope: str, *, locations: SettingsLocations) -> PluginUpdateResult:
    """
    `updatePluginOp`: the installation at `scope` (managed allowed) brought to the marketplace's
    current version, on disk only (a restart or `/reload-plugins` applies it).
    """
    parsed = parse_plugin_identifier(plugin)
    plugin_id = f"{parsed.name}@{parsed.marketplace}" if parsed.marketplace else plugin
    info = await get_plugin_by_id(plugin)
    if info is None:
        return PluginUpdateResult(False, f'Plugin "{parsed.name}" not found', plugin_id=plugin_id, scope=scope)
    installations = load_installed_plugins_from_disk()["plugins"].get(plugin_id) or []
    if not installations:
        return PluginUpdateResult(False, f'Plugin "{parsed.name}" is not installed', plugin_id=plugin_id, scope=scope)
    project_path = get_project_path_for_scope(scope, locations)
    installation = next((i for i in installations if i["scope"] == scope and i.get("projectPath") == project_path),
                        None)
    if installation is None:
        where = f"{scope} ({project_path})" if project_path else scope
        return PluginUpdateResult(False, f'Plugin "{parsed.name}" is not installed at scope {where}',
                                  plugin_id=plugin_id, scope=scope)
    return await perform_plugin_update(
        plugin_id=plugin_id, plugin_name=parsed.name, entry=info.entry,
        marketplace_install_location=info.marketplace_install_location, installation=dict(installation),
        scope=scope, project_path=project_path)


async def perform_plugin_update(
    *,
    plugin_id: str,
    plugin_name: str,
    entry: PluginMarketplaceEntry,
    marketplace_install_location: str,
    installation: dict[str, Any],
    scope: str,
    project_path: str | None,
) -> PluginUpdateResult:
    """
    `performPluginUpdate`: fetch the source (a remote one into a temporary copy), compute its
    version, and unless already current copy it to the versioned cache, record the new path on
    disk and mark the old version orphaned when nothing references it any more.
    """
    old_version = installation.get("version")
    git_commit_sha: str | None = None
    cleanup_source = False
    if not isinstance(entry["source"], str):
        cached = await cache_plugin(entry["source"], manifest=cast(PluginManifest, {"name": entry["name"]}))
        source_path, cleanup_source, git_commit_sha = cached.path, True, cached.git_commit_sha
        new_version = await calculate_plugin_version(plugin_id, entry["source"], cached.manifest, cached.path,
                                                     entry.get("version"), cached.git_commit_sha)
    else:
        if not os.path.exists(marketplace_install_location):
            return PluginUpdateResult(False, f"Marketplace directory not found at {marketplace_install_location}",
                                      plugin_id=plugin_id, scope=scope)
        marketplace_dir = (marketplace_install_location if os.path.isdir(marketplace_install_location)
                           else os.path.dirname(marketplace_install_location))
        source_path = os.path.normpath(os.path.join(marketplace_dir, entry["source"]))
        if not os.path.exists(source_path):
            return PluginUpdateResult(False, f"Plugin source not found at {source_path}", plugin_id=plugin_id,
                                      scope=scope)
        manifest: PluginManifest | None
        try:
            manifest = await load_plugin_manifest(os.path.join(source_path, PLUGIN_PACKAGE_DIR, "plugin.json"),
                                                  entry["name"], entry["source"])
        except Exception:  # noqa: BLE001 - upstream falls back to the other version sources
            manifest = None
        new_version = await calculate_plugin_version(plugin_id, entry["source"], manifest, source_path,
                                                     entry.get("version"))
    try:
        versioned_path = get_versioned_cache_path(plugin_id, new_version)
        zip_path = get_versioned_zip_cache_path(plugin_id, new_version)
        if installation.get("version") == new_version or installation.get("installPath") in (versioned_path, zip_path):
            return PluginUpdateResult(True, f"{plugin_name} is already at the latest version ({new_version}).",
                                      plugin_id=plugin_id, new_version=new_version, old_version=old_version,
                                      already_up_to_date=True, scope=scope)
        versioned_path = await copy_plugin_to_versioned_cache(source_path, plugin_id, new_version, entry)
        old_path = installation.get("installPath")
        update_installation_path_on_disk(plugin_id, cast(PluginScope, scope), project_path, versioned_path, new_version,
                                         git_commit_sha)
        if old_path and old_path != versioned_path:
            on_disk = load_installed_plugins_from_disk()["plugins"].values()
            still_referenced = any(i["installPath"] == old_path for entries in on_disk for i in entries)
            if not still_referenced:
                await mark_plugin_version_orphaned(old_path)
        where = f"{scope} ({project_path})" if project_path else scope
        return PluginUpdateResult(
            True, f'Plugin "{plugin_name}" updated from {old_version or "unknown"} to {new_version} for scope {where}. '
                  "Restart to apply changes.",
            plugin_id=plugin_id, new_version=new_version, old_version=old_version, scope=scope)
    finally:
        if cleanup_source and source_path != get_versioned_cache_path(plugin_id, new_version):
            shutil.rmtree(source_path, ignore_errors=True)


__all__ = [
    "PLUGIN_CLI",
    "VALID_INSTALLABLE_SCOPES",
    "VALID_UPDATE_SCOPES",
    "PluginOperationResult",
    "PluginUpdateResult",
    "assert_installable_scope",
    "disable_all_plugins_op",
    "disable_plugin_op",
    "enable_plugin_op",
    "find_plugin_by_identifier",
    "find_plugin_in_settings",
    "get_plugin_installation_from_v2",
    "get_project_path_for_scope",
    "install_plugin_op",
    "is_installable_scope",
    "is_plugin_enabled_at_project_scope",
    "perform_plugin_update",
    "resolve_delisted_plugin_id",
    "set_plugin_enabled_op",
    "uninstall_plugin_op",
    "update_plugin_op",
]
