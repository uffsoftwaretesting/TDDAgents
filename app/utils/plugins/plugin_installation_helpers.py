"""
Shared helpers for installing plugins: path containment, cache-and-register, the install core.

Ported from `reference/claude-code/src/utils/plugins/pluginInstallationHelpers.ts` ->
`PluginInstallationInfo`, `getCurrentTimestamp`, `validatePathWithinBase`, `cacheAndRegisterPlugin`,
`registerPluginInstallation`, `parsePluginId`, `InstallCoreResult`, `formatResolutionError`,
`installResolvedPlugin`, `InstallPluginResult`, `InstallPluginParams`, `installPluginFromMarketplace`.

`locations` names the run's settings sources; upstream's `getCwd()` (the project path recorded
for project/local installs) is `locations.project_root`.

Divergences: the `tengu_plugin_installed` analytics event (and `buildPluginTelemetryFields`) is
not ported. The zip cache is stage 2 (`app/utils/plugins/zip_cache.py`).
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from app.utils.permissions.permission_update import update_settings_for_source
from app.utils.plugins.cache_utils import clear_all_caches
from app.utils.plugins.dependency_resolver import (
    DependencyLookupResult,
    ResolutionResult,
    format_dependency_count_suffix,
    get_enabled_plugin_ids_for_scope,
    resolve_dependency_closure,
)
from app.utils.plugins.installed_plugins_manager import (
    add_installed_plugin,
    get_git_commit_sha,
)
from app.utils.plugins.marketplace_manager import (
    get_marketplace_cache_only,
    get_plugin_by_id,
)
from app.utils.plugins.plugin_identifier import (
    InstallableScope,
    parse_plugin_identifier,
    scope_to_setting_source,
)
from app.utils.plugins.plugin_loader import (
    cache_plugin,
    get_versioned_cache_path,
    get_versioned_zip_cache_path,
)
from app.utils.plugins.plugin_policy import is_plugin_blocked_by_policy
from app.utils.plugins.plugin_versioning import calculate_plugin_version
from app.utils.plugins.schemas import (
    InstalledPlugin,
    PluginMarketplaceEntry,
    PluginScope,
    is_local_plugin_source,
)
from app.utils.plugins.zip_cache import (
    convert_directory_to_zip_in_place,
    is_plugin_zip_cache_enabled,
    require_zip_cache_port,
)
from app.utils.settings.settings import SettingsLocations, get_settings_for_source

logger = logging.getLogger("TDDOrchestrator.Plugins")


@dataclass(frozen=True)
class PluginInstallationInfo:
    """`PluginInstallationInfo`."""

    plugin_id: str
    install_path: str
    version: str | None = None


def get_current_timestamp() -> str:
    """`getCurrentTimestamp`: ISO 8601 with milliseconds, as `Date.toISOString`."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate_path_within_base(base_path: str, relative_path: str) -> str:
    """`validatePathWithinBase`: the resolved path, or raise if it escapes `base_path`."""
    resolved = os.path.abspath(os.path.join(base_path, relative_path))
    base = os.path.abspath(base_path)
    if not resolved.startswith(base + os.sep) and resolved != base:
        raise ValueError(f'Path traversal detected: "{relative_path}" would escape the base directory')
    return resolved


async def cache_and_register_plugin(
    plugin_id: str,
    entry: PluginMarketplaceEntry,
    scope: PluginScope = "user",
    project_path: str | None = None,
    local_source_path: str | None = None,
) -> str:
    """
    `cacheAndRegisterPlugin`: cache the plugin, compute its version, move it to
    `cache/<marketplace>/<plugin>/<version>/` and record it in installed_plugins.json.
    """
    require_zip_cache_port()
    source: Any = local_source_path if isinstance(entry["source"], str) and local_source_path else entry["source"]
    cached = await cache_plugin(source, manifest=entry)
    path_for_git_sha = local_source_path or cached.path
    git_commit_sha = cached.git_commit_sha or await get_git_commit_sha(path_for_git_sha)
    now = get_current_timestamp()
    version = await calculate_plugin_version(
        plugin_id,
        entry["source"],
        cached.manifest,
        path_for_git_sha,
        entry.get("version"),
        cached.git_commit_sha,
    )
    versioned_path = get_versioned_cache_path(plugin_id, version)
    final_path = cached.path
    if cached.path != versioned_path:
        os.makedirs(os.path.dirname(versioned_path), exist_ok=True)
        shutil.rmtree(versioned_path, ignore_errors=True)
        normalized = cached.path if cached.path.endswith(os.sep) else cached.path + os.sep
        if versioned_path.startswith(normalized):
            # Marketplace name equals plugin name: the target is inside the cached directory.
            temp_path = os.path.join(
                os.path.dirname(cached.path),
                f".claude-plugin-temp-{int(time.time() * 1000)}-{secrets.token_hex(4)}",
            )
            os.rename(cached.path, temp_path)
            os.makedirs(os.path.dirname(versioned_path), exist_ok=True)
            os.rename(temp_path, versioned_path)
        else:
            os.rename(cached.path, versioned_path)
        final_path = versioned_path
    if is_plugin_zip_cache_enabled():
        zip_path = get_versioned_zip_cache_path(plugin_id, version)
        await convert_directory_to_zip_in_place(final_path, zip_path)
        final_path = zip_path
    metadata: InstalledPlugin = {
        "version": version,
        "installedAt": now,
        "lastUpdated": now,
        "installPath": final_path,
    }
    if git_commit_sha:
        metadata["gitCommitSha"] = git_commit_sha
    add_installed_plugin(plugin_id, metadata, scope, project_path)
    return final_path


def register_plugin_installation(
    info: PluginInstallationInfo,
    scope: PluginScope = "user",
    project_path: str | None = None,
) -> None:
    """`registerPluginInstallation`: record a plugin already on disk, without caching."""
    now = get_current_timestamp()
    add_installed_plugin(
        info.plugin_id,
        {
            "version": info.version or "unknown",
            "installedAt": now,
            "lastUpdated": now,
            "installPath": info.install_path,
        },
        scope,
        project_path,
    )


@dataclass(frozen=True)
class ParsedPluginId:
    """`parsePluginId`'s result."""

    name: str
    marketplace: str


def parse_plugin_id(plugin_id: str) -> ParsedPluginId | None:
    """`parsePluginId`: exactly `name@marketplace`, both non-empty."""
    parts = plugin_id.split("@")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return ParsedPluginId(name=parts[0], marketplace=parts[1])


@dataclass(frozen=True)
class InstallCoreResult:
    """
    `InstallCoreResult`: `ok` with `closure` and `dep_note`, or a failure `reason`
    (`local-source-no-location`, `settings-write-failed` with `message`, `resolution-failed`
    with `resolution`, `blocked-by-policy`, `dependency-blocked-by-policy` with
    `blocked_dependency`), each naming `plugin_name` where upstream does.
    """

    ok: bool
    closure: tuple[str, ...] = ()
    dep_note: str = ""
    reason: (
        Literal[
            "local-source-no-location",
            "settings-write-failed",
            "resolution-failed",
            "blocked-by-policy",
            "dependency-blocked-by-policy",
        ]
        | None
    ) = None
    plugin_name: str | None = None
    message: str | None = None
    resolution: ResolutionResult | None = None
    blocked_dependency: str | None = None


def format_resolution_error(r: ResolutionResult) -> str:
    """`formatResolutionError`: a failed resolution as a user-facing message."""
    if r.reason == "cycle":
        return f"Dependency cycle: {' → '.join(r.chain)}"
    if r.reason == "cross-marketplace":
        dep_mkt = parse_plugin_identifier(r.dependency or "").marketplace
        where = f'marketplace "{dep_mkt}"' if dep_mkt else "a different marketplace"
        hint = (
            f' Add "{dep_mkt}" to allowCrossMarketplaceDependenciesOn in the ROOT marketplace\'s marketplace.json '
            "(the marketplace of the plugin you're installing — only its allowlist applies; no transitive trust)."
            if dep_mkt
            else ""
        )
        return (
            f'Dependency "{r.dependency}" (required by {r.required_by}) is in {where}, which is not in the '
            f"allowlist — cross-marketplace dependencies are blocked by default. Install it manually first.{hint}"
        )
    dep_mkt = parse_plugin_identifier(r.missing or "").marketplace
    if dep_mkt:
        return (
            f'Dependency "{r.missing}" (required by {r.required_by}) not found. '
            f'Is the "{dep_mkt}" marketplace added?'
        )
    return f'Dependency "{r.missing}" (required by {r.required_by}) not found in any configured marketplace'


@dataclass
class _DepInfo:
    entry: PluginMarketplaceEntry
    marketplace_install_location: str


async def install_resolved_plugin(
    *,
    plugin_id: str,
    entry: PluginMarketplaceEntry,
    scope: InstallableScope,
    locations: SettingsLocations,
    marketplace_install_location: str | None = None,
) -> InstallCoreResult:
    """
    `installResolvedPlugin`: the policy guard, the dependency closure, one settings write
    enabling the closure, the cache of each member, then the caches cleared.
    """
    setting_source = scope_to_setting_source(scope)
    if is_plugin_blocked_by_policy(plugin_id, locations):
        return InstallCoreResult(ok=False, reason="blocked-by-policy", plugin_name=entry["name"])

    dep_info: dict[str, _DepInfo] = {}
    if is_local_plugin_source(entry["source"]) and not marketplace_install_location:
        return InstallCoreResult(ok=False, reason="local-source-no-location", plugin_name=entry["name"])
    if marketplace_install_location:
        dep_info[plugin_id] = _DepInfo(entry, marketplace_install_location)

    root_marketplace = parse_plugin_identifier(plugin_id).marketplace
    allowed: list[str] = []
    if root_marketplace:
        root = await get_marketplace_cache_only(root_marketplace)
        if root is not None:
            allowed = list(root.get("allowCrossMarketplaceDependenciesOn") or [])

    async def lookup(dep_id: str) -> DependencyLookupResult | None:
        found: Mapping[str, Any] | None
        if dep_id in dep_info:
            found = dep_info[dep_id].entry
        elif dep_id == plugin_id:
            found = entry
        else:
            info = await get_plugin_by_id(dep_id)
            if info is not None:
                dep_info[dep_id] = _DepInfo(info.entry, info.marketplace_install_location)
            found = info.entry if info is not None else None
        if found is None:
            return None
        return DependencyLookupResult(dependencies=tuple(found.get("dependencies") or ()))

    resolution = await resolve_dependency_closure(
        plugin_id,
        lookup,
        get_enabled_plugin_ids_for_scope(setting_source, locations),
        set(allowed),
    )
    if not resolution.ok:
        return InstallCoreResult(ok=False, reason="resolution-failed", resolution=resolution)

    for dep_id in resolution.closure:
        if dep_id != plugin_id and is_plugin_blocked_by_policy(dep_id, locations):
            return InstallCoreResult(
                ok=False,
                reason="dependency-blocked-by-policy",
                plugin_name=entry["name"],
                blocked_dependency=dep_id,
            )

    current = (get_settings_for_source(setting_source, locations) or {}).get("enabledPlugins")
    enabled_plugins = dict(current) if isinstance(current, dict) else {}
    enabled_plugins.update({dep_id: True for dep_id in resolution.closure})
    error = update_settings_for_source(setting_source, locations, {"enabledPlugins": enabled_plugins})
    if error is not None:
        return InstallCoreResult(ok=False, reason="settings-write-failed", message=error)

    project_path = str(locations.project_root) if scope != "user" else None
    for dep_id in resolution.closure:
        info_for = dep_info.get(dep_id)
        if info_for is None and dep_id == plugin_id:
            looked_up = await get_plugin_by_id(dep_id)
            if looked_up is not None and looked_up.marketplace_install_location:
                info_for = _DepInfo(entry, looked_up.marketplace_install_location)
        if info_for is None:
            continue
        local_source_path: str | None = None
        source = info_for.entry["source"]
        if isinstance(source, str) and is_local_plugin_source(source):
            local_source_path = validate_path_within_base(info_for.marketplace_install_location, source)
        await cache_and_register_plugin(dep_id, info_for.entry, scope, project_path, local_source_path)

    clear_all_caches()
    dep_note = format_dependency_count_suffix([d for d in resolution.closure if d != plugin_id])
    return InstallCoreResult(ok=True, closure=resolution.closure, dep_note=dep_note)


@dataclass(frozen=True)
class InstallPluginResult:
    """`InstallPluginResult`: `success` with `message`, or `error`."""

    success: bool
    message: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class InstallPluginParams:
    """`InstallPluginParams`."""

    plugin_id: str
    entry: PluginMarketplaceEntry
    marketplace_name: str
    scope: InstallableScope = "user"
    trigger: Literal["hint", "user"] = "user"


async def install_plugin_from_marketplace(
    params: InstallPluginParams, *, locations: SettingsLocations
) -> InstallPluginResult:
    """`installPluginFromMarketplace`: the interactive wrapper around `install_resolved_plugin`."""
    entry = params.entry
    try:
        info = await get_plugin_by_id(params.plugin_id)
        result = await install_resolved_plugin(
            plugin_id=params.plugin_id,
            entry=entry,
            scope=params.scope,
            locations=locations,
            marketplace_install_location=info.marketplace_install_location if info is not None else None,
        )
        if not result.ok:
            if result.reason == "local-source-no-location":
                return InstallPluginResult(
                    success=False,
                    error=f'Cannot install local plugin "{result.plugin_name}" without marketplace install location',
                )
            if result.reason == "settings-write-failed":
                return InstallPluginResult(success=False, error=f"Failed to update settings: {result.message}")
            if result.reason == "resolution-failed" and result.resolution is not None:
                return InstallPluginResult(success=False, error=format_resolution_error(result.resolution))
            if result.reason == "blocked-by-policy":
                return InstallPluginResult(
                    success=False,
                    error=(
                        f'Plugin "{result.plugin_name}" is blocked by your organization\'s policy and cannot be '
                        "installed"
                    ),
                )
            return InstallPluginResult(
                success=False,
                error=(
                    f'Cannot install "{result.plugin_name}": dependency "{result.blocked_dependency}" is blocked by '
                    "your organization's policy"
                ),
            )
        return InstallPluginResult(
            success=True,
            message=f"✓ Installed {entry['name']}{result.dep_note}. Run /reload-plugins to activate.",
        )
    except Exception as exc:  # noqa: BLE001 - upstream reports any failure as a result, not a raise
        logger.error("Failed to install %s: %s", params.plugin_id, exc)
        return InstallPluginResult(success=False, error=f"Failed to install: {exc}")


__all__ = [
    "InstallCoreResult",
    "InstallPluginParams",
    "InstallPluginResult",
    "ParsedPluginId",
    "PluginInstallationInfo",
    "cache_and_register_plugin",
    "format_resolution_error",
    "get_current_timestamp",
    "install_plugin_from_marketplace",
    "install_resolved_plugin",
    "parse_plugin_id",
    "register_plugin_installation",
    "validate_path_within_base",
]
