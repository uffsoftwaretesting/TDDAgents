"""
Plugin installation metadata in `installed_plugins.json`: which plugins are on disk, at which
scope, version and path. Enabled/disabled state stays in the settings files.

Ported from `reference/claude-code/src/utils/plugins/installedPluginsManager.ts` ->
`getInstalledPluginsFilePath`, `getInstalledPluginsV2FilePath`, `clearInstalledPluginsCache`,
`migrateToSinglePluginFile`, `cleanupLegacyCache`, `resetMigrationState`,
`readInstalledPluginsFileRaw`, `migrateV1ToV2`, `loadInstalledPluginsV2`, `saveInstalledPluginsV2`,
`addPluginInstallation`, `removePluginInstallation`, `getInMemoryInstalledPlugins`,
`loadInstalledPluginsFromDisk`, `updateInstallationPathOnDisk`, `hasPendingUpdates`,
`getPendingUpdateCount`, `getPendingUpdatesDetails`, `resetInMemoryState`,
`initializeVersionedPlugins`, `removeAllPluginsForMarketplace`,
`isInstallationRelevantToCurrentProject`, `isPluginInstalled`, `isPluginGloballyInstalled`,
`addInstalledPlugin`, `removeInstalledPlugin`, `deletePluginCache`, `getGitCommitSha`,
`getPluginVersionFromManifest`, `migrateFromEnabledPlugins`.

The file is user-global (`<plugins dir>/installed_plugins.json`, V2: plugin id -> one entry per
scope). Two module-level states mirror upstream's: a read cache, and the session snapshot
(`get_in_memory_installed_plugins`) that background updates never touch.

`locations` names the run's settings sources where upstream reads its process-wide settings;
upstream's `getCwd()` / `getOriginalCwd()` (the project) is `locations.project_root`. Optional
fields absent upstream (`undefined`, dropped by `JSON.stringify`) are omitted from the dicts.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from app.utils.fs_operations import rm
from app.utils.git.git_filesystem import get_head_for_dir
from app.utils.plugins.marketplace_manager import get_plugin_by_id
from app.utils.plugins.plugin_directories import PLUGIN_PACKAGE_DIR, get_plugins_directory
from app.utils.plugins.plugin_identifier import (
    EDITABLE_SETTING_SOURCES,
    InstallableScope,
    parse_plugin_identifier,
    setting_source_to_scope,
)
from app.utils.plugins.plugin_loader import (
    get_plugin_cache_path,
    get_versioned_cache_path,
)
from app.utils.plugins.schemas import (
    InstalledPlugin,
    InstalledPluginsFileSchemaV1,
    InstalledPluginsFileSchemaV2,
    InstalledPluginsFileV1,
    InstalledPluginsFileV2,
    PluginInstallationEntry,
    PluginScope,
    parse,
    safe_parse,
)
from app.utils.settings.settings import (
    SettingsLocations,
    get_initial_settings,
    get_settings_for_source,
)

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `PersistableScope`: every schema scope.
PersistableScope = PluginScope

INSTALLED_PLUGINS_FILENAME = "installed_plugins.json"
INSTALLED_PLUGINS_V2_FILENAME = "installed_plugins_v2.json"

_migration_completed = False
_installed_plugins_cache_v2: InstalledPluginsFileV2 | None = None
_in_memory_installed_plugins: InstalledPluginsFileV2 | None = None

_UNSAFE_NAME = re.compile(r"[^a-zA-Z0-9-_]")


def _now() -> str:
    """`new Date().toISOString()`."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _empty() -> InstalledPluginsFileV2:
    return {"version": 2, "plugins": {}}


def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False))
        handle.flush()
        os.fsync(handle.fileno())


def get_installed_plugins_file_path() -> str:
    """`getInstalledPluginsFilePath`."""
    return os.path.join(get_plugins_directory(), INSTALLED_PLUGINS_FILENAME)


def get_installed_plugins_v2_file_path() -> str:
    """`getInstalledPluginsV2FilePath`: the legacy file, read only by the migration."""
    return os.path.join(get_plugins_directory(), INSTALLED_PLUGINS_V2_FILENAME)


def clear_installed_plugins_cache() -> None:
    """`clearInstalledPluginsCache`: the read cache and the session snapshot."""
    global _installed_plugins_cache_v2, _in_memory_installed_plugins
    _installed_plugins_cache_v2 = None
    _in_memory_installed_plugins = None
    logger.debug("Cleared installed plugins cache")


def reset_migration_state() -> None:
    """`resetMigrationState` (tests)."""
    global _migration_completed
    _migration_completed = False


def migrate_to_single_plugin_file() -> None:
    """
    `migrateToSinglePluginFile`, once per session: rename `installed_plugins_v2.json` over the
    main file, or convert a V1 main file to V2 in place; then clean the legacy cache.
    """
    global _migration_completed
    if _migration_completed:
        return
    main_path = get_installed_plugins_file_path()
    v2_path = get_installed_plugins_v2_file_path()
    try:
        try:
            os.rename(v2_path, main_path)
            logger.debug("Renamed installed_plugins_v2.json to installed_plugins.json")
            _cleanup_legacy_cache(load_installed_plugins_v2())
            _migration_completed = True
            return
        except FileNotFoundError:
            pass
        try:
            with open(main_path, encoding="utf-8", errors="replace") as handle:
                main_content = handle.read()
        except FileNotFoundError:
            _migration_completed = True
            return
        main_data = json.loads(main_content)
        version = main_data.get("version") if isinstance(main_data, dict) else None
        version = version if isinstance(version, (int, float)) and not isinstance(version, bool) else 1
        if version == 1:
            v1_data = parse(InstalledPluginsFileSchemaV1, main_data)
            v2_data = _migrate_v1_to_v2(v1_data)
            _write_json(main_path, v2_data)
            logger.debug(
                "Converted installed_plugins.json from V1 to V2 format (%d plugins)", len(v1_data["plugins"])
            )
            _cleanup_legacy_cache(v2_data)
        _migration_completed = True
    except Exception as exc:  # noqa: BLE001 - upstream catches everything and marks the migration done
        logger.error("Failed to migrate plugin files: %s", exc)
        _migration_completed = True


def _cleanup_legacy_cache(v2_data: InstalledPluginsFileV2) -> None:
    """`cleanupLegacyCache`: remove unreferenced flat `<cache>/<plugin>/` directories."""
    cache_path = get_plugin_cache_path()
    try:
        referenced = {e["installPath"] for entries in v2_data["plugins"].values() for e in entries}
        with os.scandir(cache_path) as iterator:
            top = [e for e in iterator if e.is_dir(follow_symlinks=False)]
        for entry in top:
            entry_path = os.path.join(cache_path, entry.name)
            versioned = False
            with os.scandir(entry_path) as sub_iterator:
                for sub in sub_iterator:
                    if not sub.is_dir(follow_symlinks=False):
                        continue
                    with os.scandir(sub.path) as version_iterator:
                        if any(v.is_dir(follow_symlinks=False) for v in version_iterator):
                            versioned = True
                            break
            if versioned:
                continue
            if entry_path not in referenced:
                rm(entry_path, recursive=True, force=True)
                logger.debug("Cleaned up legacy cache directory: %s", entry.name)
    except OSError as exc:
        logger.warning("Failed to clean up legacy cache: %s", exc)


@dataclass(frozen=True)
class _RawFile:
    version: int
    data: Any


def _read_installed_plugins_file_raw() -> _RawFile | None:
    """`readInstalledPluginsFileRaw`: `None` when absent; a parse error raises."""
    try:
        with open(get_installed_plugins_file_path(), encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except FileNotFoundError:
        return None
    data = json.loads(content)
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, (int, float)) or isinstance(version, bool):
        version = 1
    return _RawFile(version=int(version) if version == int(version) else -1, data=data)


def _entry(
    scope: PluginScope,
    install_path: str,
    *,
    version: str | None = None,
    installed_at: str | None = None,
    last_updated: str | None = None,
    git_commit_sha: str | None = None,
    project_path: str | None = None,
) -> PluginInstallationEntry:
    entry: PluginInstallationEntry = {"scope": scope, "installPath": install_path}
    if version is not None:
        entry["version"] = version
    if installed_at is not None:
        entry["installedAt"] = installed_at
    if last_updated is not None:
        entry["lastUpdated"] = last_updated
    if git_commit_sha is not None:
        entry["gitCommitSha"] = git_commit_sha
    if project_path:
        entry["projectPath"] = project_path
    return entry


def _migrate_v1_to_v2(v1_data: InstalledPluginsFileV1) -> InstalledPluginsFileV2:
    """`migrateV1ToV2`: every V1 plugin at user scope, at its versioned cache path."""
    plugins: dict[str, list[PluginInstallationEntry]] = {}
    for plugin_id, plugin in v1_data["plugins"].items():
        plugins[plugin_id] = [
            _entry(
                "user",
                get_versioned_cache_path(plugin_id, plugin["version"]),
                version=plugin["version"],
                installed_at=plugin["installedAt"],
                last_updated=plugin.get("lastUpdated"),
                git_commit_sha=plugin.get("gitCommitSha"),
            )
        ]
    return {"version": 2, "plugins": plugins}


def _parse_raw(raw: _RawFile) -> InstalledPluginsFileV2:
    if raw.version == 2:
        return parse(InstalledPluginsFileSchemaV2, raw.data)
    return _migrate_v1_to_v2(parse(InstalledPluginsFileSchemaV1, raw.data))


def load_installed_plugins_v2() -> InstalledPluginsFileV2:
    """`loadInstalledPluginsV2`: cached; V1 converted in memory; any failure is an empty state."""
    global _installed_plugins_cache_v2
    if _installed_plugins_cache_v2 is not None:
        return _installed_plugins_cache_v2
    path = get_installed_plugins_file_path()
    try:
        raw = _read_installed_plugins_file_raw()
        if raw is not None:
            data = _parse_raw(raw)
            _installed_plugins_cache_v2 = data
            logger.debug("Loaded %d installed plugins from %s", len(data["plugins"]), path)
            return data
        logger.debug("installed_plugins.json doesn't exist, returning empty V2 object")
    except Exception as exc:  # noqa: BLE001 - upstream starts with an empty state on any failure
        logger.error("Failed to load installed_plugins.json: %s. Starting with empty state.", exc)
    _installed_plugins_cache_v2 = _empty()
    return _installed_plugins_cache_v2


def _save_installed_plugins_v2(data: InstalledPluginsFileV2) -> None:
    """`saveInstalledPluginsV2`: write the file and refresh the read cache; failures raise."""
    global _installed_plugins_cache_v2
    os.makedirs(get_plugins_directory(), exist_ok=True)
    _write_json(get_installed_plugins_file_path(), data)
    _installed_plugins_cache_v2 = data
    logger.debug("Saved %d installed plugins to %s", len(data["plugins"]), get_installed_plugins_file_path())


def _find_index(entries: list[PluginInstallationEntry], scope: str, project_path: str | None) -> int:
    for i, entry in enumerate(entries):
        if entry["scope"] == scope and entry.get("projectPath") == project_path:
            return i
    return -1


def add_plugin_installation(
    plugin_id: str,
    scope: PersistableScope,
    install_path: str,
    metadata: dict[str, Any],
    project_path: str | None = None,
) -> None:
    """`addPluginInstallation`: add or replace the entry for this scope and project."""
    data = load_installed_plugins_from_disk()
    installations = data["plugins"].get(plugin_id) or []
    new_entry = _entry(
        scope,
        install_path,
        version=metadata.get("version"),
        installed_at=metadata.get("installedAt") or _now(),
        last_updated=_now(),
        git_commit_sha=metadata.get("gitCommitSha"),
        project_path=project_path,
    )
    index = _find_index(installations, scope, project_path)
    if index >= 0:
        installations[index] = new_entry
        logger.debug("Updated installation for %s at scope %s", plugin_id, scope)
    else:
        installations.append(new_entry)
        logger.debug("Added installation for %s at scope %s", plugin_id, scope)
    data["plugins"][plugin_id] = installations
    _save_installed_plugins_v2(data)


def remove_plugin_installation(plugin_id: str, scope: PersistableScope, project_path: str | None = None) -> None:
    """`removePluginInstallation`: drop the scope's entry; the plugin goes when none remain."""
    data = load_installed_plugins_from_disk()
    installations = data["plugins"].get(plugin_id)
    if installations is None:
        return
    remaining = [e for e in installations if not (e["scope"] == scope and e.get("projectPath") == project_path)]
    if remaining:
        data["plugins"][plugin_id] = remaining
    else:
        del data["plugins"][plugin_id]
    _save_installed_plugins_v2(data)
    logger.debug("Removed installation for %s at scope %s", plugin_id, scope)


def get_in_memory_installed_plugins() -> InstalledPluginsFileV2:
    """`getInMemoryInstalledPlugins`: the session snapshot, loaded once."""
    global _in_memory_installed_plugins
    if _in_memory_installed_plugins is None:
        _in_memory_installed_plugins = load_installed_plugins_v2()
    return _in_memory_installed_plugins


def load_installed_plugins_from_disk() -> InstalledPluginsFileV2:
    """`loadInstalledPluginsFromDisk`: fresh from disk, bypassing every cache; failures are empty."""
    try:
        raw = _read_installed_plugins_file_raw()
        if raw is not None:
            return _parse_raw(raw)
        return _empty()
    except Exception as exc:  # noqa: BLE001 - upstream returns an empty state on any failure
        logger.error("Failed to load installed plugins from disk: %s", exc)
        return _empty()


def update_installation_path_on_disk(
    plugin_id: str,
    scope: PersistableScope,
    project_path: str | None,
    new_path: str,
    new_version: str,
    git_commit_sha: str | None = None,
) -> None:
    """`updateInstallationPathOnDisk`: record a new version on disk only; the session snapshot is kept."""
    global _installed_plugins_cache_v2
    disk = load_installed_plugins_from_disk()
    installations = disk["plugins"].get(plugin_id)
    if not installations:
        logger.debug("Cannot update %s on disk: plugin not found in installed plugins", plugin_id)
        return
    index = _find_index(installations, scope, project_path)
    if index < 0:
        logger.debug("Cannot update %s on disk: no installation for scope %s", plugin_id, scope)
        return
    entry = installations[index]
    entry["installPath"] = new_path
    entry["version"] = new_version
    entry["lastUpdated"] = _now()
    if git_commit_sha is not None:
        entry["gitCommitSha"] = git_commit_sha
    _write_json(get_installed_plugins_file_path(), disk)
    _installed_plugins_cache_v2 = None
    logger.debug("Updated %s on disk to version %s at %s", plugin_id, new_version, new_path)


@dataclass(frozen=True)
class PendingUpdate:
    """One `getPendingUpdatesDetails` item."""

    plugin_id: str
    scope: str
    old_version: str
    new_version: str


def get_pending_updates_details() -> list[PendingUpdate]:
    """`getPendingUpdatesDetails`: installations whose disk path differs from the session's."""
    memory = get_in_memory_installed_plugins()
    disk = load_installed_plugins_from_disk()
    updates: list[PendingUpdate] = []
    for plugin_id, disk_entries in disk["plugins"].items():
        memory_entries = memory["plugins"].get(plugin_id)
        if not memory_entries:
            continue
        for disk_entry in disk_entries:
            index = _find_index(memory_entries, disk_entry["scope"], disk_entry.get("projectPath"))
            if index < 0:
                continue
            memory_entry = memory_entries[index]
            if memory_entry["installPath"] != disk_entry["installPath"]:
                updates.append(PendingUpdate(
                    plugin_id=plugin_id,
                    scope=disk_entry["scope"],
                    old_version=memory_entry.get("version") or "unknown",
                    new_version=disk_entry.get("version") or "unknown",
                ))
    return updates


def has_pending_updates() -> bool:
    """`hasPendingUpdates`."""
    return bool(get_pending_updates_details())


def get_pending_update_count() -> int:
    """`getPendingUpdateCount`."""
    return len(get_pending_updates_details())


def reset_in_memory_state() -> None:
    """`resetInMemoryState`."""
    global _in_memory_installed_plugins
    _in_memory_installed_plugins = None


async def initialize_versioned_plugins(locations: SettingsLocations) -> None:
    """`initializeVersionedPlugins`: migrate, sync from `enabledPlugins`, take the session snapshot."""
    migrate_to_single_plugin_file()
    try:
        await migrate_from_enabled_plugins(locations)
    except Exception as exc:  # noqa: BLE001 - upstream logs and carries on
        logger.error("Failed to sync installed plugins from settings: %s", exc)
    data = get_in_memory_installed_plugins()
    logger.debug("Initialized versioned plugins system with %d plugins", len(data["plugins"]))


@dataclass(frozen=True)
class RemovedPlugins:
    """`removeAllPluginsForMarketplace`'s result."""

    orphaned_paths: list[str]
    removed_plugin_ids: list[str]


def remove_all_plugins_for_marketplace(name: str) -> RemovedPlugins:
    """`removeAllPluginsForMarketplace`: drop every `*@<name>` entry, saving once."""
    if not name:
        return RemovedPlugins(orphaned_paths=[], removed_plugin_ids=[])
    data = load_installed_plugins_from_disk()
    suffix = f"@{name}"
    orphaned: list[str] = []
    removed: list[str] = []
    for plugin_id in list(data["plugins"]):
        if not plugin_id.endswith(suffix):
            continue
        for entry in data["plugins"].get(plugin_id) or []:
            if entry.get("installPath") and entry["installPath"] not in orphaned:
                orphaned.append(entry["installPath"])
        del data["plugins"][plugin_id]
        removed.append(plugin_id)
        logger.debug("Removed installed plugin for marketplace removal: %s", plugin_id)
    if removed:
        _save_installed_plugins_v2(data)
    return RemovedPlugins(orphaned_paths=orphaned, removed_plugin_ids=removed)


def is_installation_relevant_to_current_project(
    installation: PluginInstallationEntry, locations: SettingsLocations
) -> bool:
    """`isInstallationRelevantToCurrentProject`: user/managed always; project/local for this project only."""
    return (
        installation["scope"] in ("user", "managed")
        or installation.get("projectPath") == str(locations.project_root)
    )


def _enabled_plugins(locations: SettingsLocations) -> dict[str, Any]:
    enabled = get_initial_settings(locations).get("enabledPlugins")
    return enabled if isinstance(enabled, dict) else {}


def is_plugin_installed(plugin_id: str, locations: SettingsLocations) -> bool:
    """`isPluginInstalled`: installed in a way relevant here, and still listed in `enabledPlugins`."""
    installations = load_installed_plugins_v2()["plugins"].get(plugin_id)
    if not installations:
        return False
    if not any(is_installation_relevant_to_current_project(i, locations) for i in installations):
        return False
    return plugin_id in _enabled_plugins(locations)


def is_plugin_globally_installed(plugin_id: str, locations: SettingsLocations) -> bool:
    """`isPluginGloballyInstalled`: a user or managed installation, still listed in `enabledPlugins`."""
    installations = load_installed_plugins_v2()["plugins"].get(plugin_id)
    if not installations:
        return False
    if not any(e["scope"] in ("user", "managed") for e in installations):
        return False
    return plugin_id in _enabled_plugins(locations)


def add_installed_plugin(
    plugin_id: str,
    metadata: InstalledPlugin,
    scope: PersistableScope = "user",
    project_path: str | None = None,
) -> None:
    """`addInstalledPlugin`: add or replace this scope's entry, keeping the other scopes'."""
    data = load_installed_plugins_from_disk()
    new_entry = _entry(
        scope,
        metadata["installPath"],
        version=metadata.get("version"),
        installed_at=metadata.get("installedAt"),
        last_updated=metadata.get("lastUpdated"),
        git_commit_sha=metadata.get("gitCommitSha"),
        project_path=project_path,
    )
    installations = data["plugins"].get(plugin_id) or []
    index = _find_index(installations, scope, project_path)
    if index >= 0:
        installations[index] = new_entry
    else:
        installations.append(new_entry)
    data["plugins"][plugin_id] = installations
    _save_installed_plugins_v2(data)
    logger.debug("%s installed plugin: %s (scope: %s)", "Updated" if index >= 0 else "Added", plugin_id, scope)


def remove_installed_plugin(plugin_id: str) -> InstalledPlugin | None:
    """`removeInstalledPlugin`: drop every installation; returns V1-style metadata of the first."""
    data = load_installed_plugins_from_disk()
    installations = data["plugins"].get(plugin_id)
    if not installations:
        return None
    first = installations[0]
    metadata: InstalledPlugin = {
        "version": first.get("version") or "unknown",
        "installedAt": first.get("installedAt") or _now(),
        "installPath": first["installPath"],
    }
    if first.get("lastUpdated") is not None:
        metadata["lastUpdated"] = first["lastUpdated"]
    if first.get("gitCommitSha") is not None:
        metadata["gitCommitSha"] = first["gitCommitSha"]
    del data["plugins"][plugin_id]
    _save_installed_plugins_v2(data)
    logger.debug("Removed installed plugin: %s", plugin_id)
    return metadata


def delete_plugin_cache(install_path: str) -> None:
    """`deletePluginCache`: remove the directory, then its plugin directory if left empty."""
    try:
        rm(install_path, recursive=True, force=True)
    except OSError as exc:
        raise RuntimeError(f"Failed to delete plugin cache at {install_path}: {exc}") from exc
    logger.debug("Deleted plugin cache at %s", install_path)
    cache_path = get_plugin_cache_path()
    if "/cache/" in install_path and install_path.startswith(cache_path):
        plugin_dir = os.path.dirname(install_path)
        if plugin_dir != cache_path and plugin_dir.startswith(cache_path):
            try:
                if not os.listdir(plugin_dir):
                    os.rmdir(plugin_dir)
                    logger.debug("Deleted empty plugin directory at %s", plugin_dir)
            except OSError:
                pass


async def get_git_commit_sha(dir_path: str) -> str | None:
    """`getGitCommitSha` (this module's): the HEAD SHA, or `None`."""
    return await get_head_for_dir(dir_path)


def _get_plugin_version_from_manifest(plugin_cache_path: str, plugin_id: str) -> str:
    """`getPluginVersionFromManifest`: `<PLUGIN_PACKAGE_DIR>/plugin.json`'s `version`, else `unknown`."""
    try:
        manifest_path = os.path.join(plugin_cache_path, PLUGIN_PACKAGE_DIR, "plugin.json")
        with open(manifest_path, encoding="utf-8", errors="replace") as handle:
            manifest = json.loads(handle.read())
        version = manifest.get("version") if isinstance(manifest, dict) else None
        return str(version) if version else "unknown"
    except (OSError, ValueError):
        logger.debug("Could not read version from manifest for %s", plugin_id)
        return "unknown"


async def migrate_from_enabled_plugins(locations: SettingsLocations) -> None:
    """
    `migrateFromEnabledPlugins`: make installed_plugins.json cover every `plugin@marketplace` in
    the settings' `enabledPlugins`. The settings files are the truth for scope (the most specific
    wins); a plugin missing from the file is looked up in its marketplace.
    """
    enabled = _enabled_plugins(locations)
    if not enabled:
        return
    raw = _read_installed_plugins_file_raw()
    file_exists = raw is not None
    if raw is not None and raw.version == 2:
        existing = safe_parse(InstalledPluginsFileSchemaV2, raw.data)
        if existing.success and existing.data is not None:
            plugins = existing.data["plugins"]
            if all(plugins.get(pid) for pid in enabled if "@" in pid):
                logger.debug("All plugins already exist, skipping migration")
                return
    logger.debug(
        "Syncing installed_plugins.json with enabledPlugins from all settings.json files"
        if file_exists
        else "Creating installed_plugins.json from settings.json files"
    )
    now = _now()
    project_path = str(locations.project_root)
    scopes: dict[str, tuple[InstallableScope, str | None]] = {}
    for source in EDITABLE_SETTING_SOURCES:
        source_settings = get_settings_for_source(source, locations) or {}
        source_enabled = source_settings.get("enabledPlugins")
        if not isinstance(source_enabled, dict):
            continue
        for plugin_id in source_enabled:
            if "@" not in plugin_id:
                continue
            scope = setting_source_to_scope(source)
            scopes[plugin_id] = (scope, None if scope == "user" else project_path)

    v2_plugins: dict[str, list[PluginInstallationEntry]] = {}
    if file_exists:
        v2_plugins = dict(load_installed_plugins_v2()["plugins"])
    updated = 0
    added = 0
    for plugin_id, (scope, scope_project) in scopes.items():
        existing_entries = v2_plugins.get(plugin_id)
        if existing_entries:
            first = existing_entries[0]
            if first["scope"] != scope or first.get("projectPath") != scope_project:
                first["scope"] = scope
                if scope_project:
                    first["projectPath"] = scope_project
                else:
                    first.pop("projectPath", None)
                first["lastUpdated"] = now
                updated += 1
                logger.debug("Updated %s scope to %s (settings.json is source of truth)", plugin_id, scope)
            continue
        parsed = parse_plugin_identifier(plugin_id)
        if not parsed.name or not parsed.marketplace:
            continue
        try:
            logger.debug("Looking up plugin %s in marketplace %s", plugin_id, parsed.marketplace)
            info = await get_plugin_by_id(plugin_id)
            if info is None:
                logger.debug("Plugin %s not found in any marketplace, skipping", plugin_id)
                continue
            entry = info.entry
            version = "unknown"
            git_commit_sha: str | None
            plugin_source = entry["source"]
            if isinstance(plugin_source, str):
                install_path = os.path.join(info.marketplace_install_location, plugin_source)
                version = _get_plugin_version_from_manifest(install_path, plugin_id)
                git_commit_sha = await get_git_commit_sha(install_path)
            else:
                plugin_cache_path = os.path.join(get_plugin_cache_path(), _UNSAFE_NAME.sub("-", parsed.name))
                try:
                    dir_entries = os.listdir(plugin_cache_path)
                except FileNotFoundError:
                    logger.debug("External plugin %s not in cache, skipping", plugin_id)
                    continue
                if PLUGIN_PACKAGE_DIR in dir_entries:
                    version = _get_plugin_version_from_manifest(plugin_cache_path, plugin_id)
                git_commit_sha = await get_git_commit_sha(plugin_cache_path)
            if version == "unknown" and entry.get("version"):
                version = str(entry["version"])
            if version == "unknown" and git_commit_sha:
                version = git_commit_sha[:12]
            v2_plugins[plugin_id] = [
                _entry(
                    scope,
                    get_versioned_cache_path(plugin_id, version),
                    version=version,
                    installed_at=now,
                    last_updated=now,
                    git_commit_sha=git_commit_sha,
                    project_path=scope_project,
                )
            ]
            added += 1
            logger.debug("Added %s with scope %s", plugin_id, scope)
        except Exception as exc:  # noqa: BLE001 - upstream skips a plugin it cannot look up
            logger.debug("Failed to add plugin %s: %s", plugin_id, exc)
    if not file_exists or updated > 0 or added > 0:
        _save_installed_plugins_v2(cast(InstalledPluginsFileV2, {"version": 2, "plugins": v2_plugins}))
        logger.debug("Sync completed: %d added, %d updated in installed_plugins.json", added, updated)


__all__ = [
    "INSTALLED_PLUGINS_FILENAME",
    "INSTALLED_PLUGINS_V2_FILENAME",
    "PendingUpdate",
    "PersistableScope",
    "RemovedPlugins",
    "add_installed_plugin",
    "add_plugin_installation",
    "clear_installed_plugins_cache",
    "delete_plugin_cache",
    "get_git_commit_sha",
    "get_in_memory_installed_plugins",
    "get_installed_plugins_file_path",
    "get_installed_plugins_v2_file_path",
    "get_pending_update_count",
    "get_pending_updates_details",
    "has_pending_updates",
    "initialize_versioned_plugins",
    "is_installation_relevant_to_current_project",
    "is_plugin_globally_installed",
    "is_plugin_installed",
    "load_installed_plugins_from_disk",
    "load_installed_plugins_v2",
    "migrate_from_enabled_plugins",
    "migrate_to_single_plugin_file",
    "remove_all_plugins_for_marketplace",
    "remove_installed_plugin",
    "remove_plugin_installation",
    "reset_in_memory_state",
    "reset_migration_state",
    "update_installation_path_on_disk",
]
