"""
The `plugin` and `plugin marketplace` subcommand handlers (`python -m app.main plugin …`).

Ported from `reference/claude-code/src/cli/handlers/plugins.ts` -> `handleMarketplaceError`,
`printValidationResult`, `pluginValidateHandler`, `pluginListHandler`, `marketplaceAddHandler`,
`marketplaceListHandler`, `marketplaceRemoveHandler`, `marketplaceUpdateHandler`,
`pluginInstallHandler`, `pluginUninstallHandler`, `pluginEnableHandler`, `pluginDisableHandler`,
`pluginUpdateHandler`; and `reference/claude-code/src/cli/exit.ts` -> `cliError`, `cliOk`.

Each handler returns the exit code (upstream exits the process) and writes through `out` / `err`.
The settings are `locations`: upstream reads its process-wide settings from the directory it runs
in, so the CLI's project is the current directory, or a session's project folder with `--thread-id`
(`app/main.py` `plugin_cli_locations`).

Divergences: the `tengu_*` analytics events are not ported, and `plugin list --available` has no
install counts (`installCounts.ts`, a network fetch, is not ported). `--cowork` keeps upstream's
hidden flag.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any, cast

from app.services.plugins.plugin_cli_commands import (
    CROSS,
    TICK,
    VALID_INSTALLABLE_SCOPES,
    VALID_UPDATE_SCOPES,
    disable_all_plugins,
    disable_plugin,
    enable_plugin,
    install_plugin,
    uninstall_plugin,
    update_plugin_cli,
)
from app.services.plugins.plugin_operations import PLUGIN_CLI
from app.types.plugin import PluginError, get_plugin_error_message
from app.utils.plugins.cache_utils import clear_all_caches
from app.utils.plugins.installed_plugins_manager import (
    is_plugin_installed,
    load_installed_plugins_v2,
)
from app.utils.plugins.marketplace_helpers import (
    create_plugin_id,
    load_marketplaces_with_graceful_degradation,
)
from app.utils.plugins.marketplace_manager import (
    add_marketplace_source,
    load_known_marketplaces_config,
    refresh_all_marketplaces,
    refresh_marketplace,
    remove_marketplace_source,
    save_marketplace_to_settings,
)
from app.utils.plugins.mcp_plugin_integration import load_plugin_mcp_servers
from app.utils.plugins.parse_marketplace_input import parse_marketplace_input
from app.utils.plugins.plugin_directories import (
    PLUGIN_PACKAGE_DIR,
    set_use_cowork_plugins,
)
from app.utils.plugins.plugin_identifier import (
    InstallableScope,
    parse_plugin_identifier,
    scope_to_setting_source,
)
from app.utils.plugins.plugin_loader import load_all_plugins
from app.utils.plugins.plugin_startup_check import get_plugin_editable_scopes
from app.utils.plugins.validate_plugin import (
    ValidationResult,
    validate_manifest,
    validate_plugin_contents,
)
from app.utils.settings.settings import SettingsLocations
from app.utils.string_utils import plural

logger = logging.getLogger("TDDOrchestrator.Plugins")

POINTER = "❯"
WARNING = "⚠"

Writer = Callable[[str], None]


def _stdout(text: str) -> None:
    print(text)


def _stderr(text: str) -> None:
    print(text, file=sys.stderr)


def handle_marketplace_error(error: BaseException, action: str, err: Writer) -> int:
    """`handleMarketplaceError`: `cliError(✘ Failed to <action>: <message>)`."""
    logger.debug("marketplace %s failed: %s", action, error)
    err(f"{CROSS} Failed to {action}: {error}")
    return 1


def print_validation_result(result: ValidationResult, out: Writer) -> None:
    """`printValidationResult`."""
    if result.errors:
        out(f"{CROSS} Found {len(result.errors)} {plural(len(result.errors), 'error')}:\n")
        for error in result.errors:
            out(f"  {POINTER} {error.path}: {error.message}")
        out("")
    if result.warnings:
        out(f"{WARNING} Found {len(result.warnings)} {plural(len(result.warnings), 'warning')}:\n")
        for warning in result.warnings:
            out(f"  {POINTER} {warning.path}: {warning.message}")
        out("")


async def plugin_validate_handler(manifest_path: str, *, cowork: bool = False, out: Writer = _stdout,
                                  err: Writer = _stderr) -> int:
    """`plugin validate <path>`: the manifest, then (inside a plugin package folder) its contents."""
    if cowork:
        set_use_cowork_plugins(True)
    try:
        result = await validate_manifest(manifest_path)
        out(f"Validating {result.file_type} manifest: {result.file_path}\n")
        print_validation_result(result, out)
        content_results: list[ValidationResult] = []
        if result.file_type == "plugin":
            manifest_dir = os.path.dirname(result.file_path)
            if os.path.basename(manifest_dir) == PLUGIN_PACKAGE_DIR:
                content_results = await validate_plugin_contents(os.path.dirname(manifest_dir))
                for content in content_results:
                    out(f"Validating {content.file_type}: {content.file_path}\n")
                    print_validation_result(content, out)
        if result.success and all(r.success for r in content_results):
            warned = bool(result.warnings) or any(r.warnings for r in content_results)
            out(f"{TICK} Validation passed with warnings" if warned else f"{TICK} Validation passed")
            return 0
        out(f"{CROSS} Validation failed")
        return 1
    except Exception as error:  # noqa: BLE001 - upstream reports an unexpected failure and exits 2
        err(f"{CROSS} Unexpected error during validation: {error}")
        return 2


def _errors_for(errors: Sequence[PluginError], source: str, name: str) -> list[PluginError]:
    return [e for e in errors if e["source"] == source or cast(dict[str, Any], e).get("plugin") == name]


async def plugin_list_handler(
    *, locations: SettingsLocations, as_json: bool = False, available: bool = False, cowork: bool = False,
    out: Writer = _stdout,
) -> int:
    """`plugin list [--json [--available]]`: the installations, then the session-only (`--plugin-dir`) plugins."""
    if cowork:
        set_use_cowork_plugins(True)
    installed = load_installed_plugins_v2()
    enabled_plugins = get_plugin_editable_scopes(locations)
    plugin_ids = sorted(installed["plugins"])
    loaded = await load_all_plugins(locations)
    all_loaded = [*loaded.enabled, *loaded.disabled]
    inline_plugins = [p for p in all_loaded if p.source.endswith("@inline")]
    inline_errors = [e for e in loaded.errors if e["source"].endswith("@inline") or e["source"].startswith("inline[")]
    path_failures = [e for e in inline_errors if e["source"].startswith("inline[")]
    if as_json:
        by_source = {p.source: p for p in all_loaded}
        plugins: list[dict[str, Any]] = []
        for plugin_id in plugin_ids:
            errors = [get_plugin_error_message(e) for e in
                      _errors_for(loaded.errors, plugin_id, parse_plugin_identifier(plugin_id).name)]
            for installation in installed["plugins"][plugin_id] or ():
                loaded_plugin = by_source.get(plugin_id)
                servers = None
                if loaded_plugin is not None:
                    servers = loaded_plugin.mcp_servers or await load_plugin_mcp_servers(loaded_plugin, [], locations)
                plugins.append(_defined({
                    "id": plugin_id, "version": installation.get("version") or "unknown",
                    "scope": installation["scope"], "enabled": plugin_id in enabled_plugins,
                    "installPath": installation["installPath"], "installedAt": installation.get("installedAt"),
                    "lastUpdated": installation.get("lastUpdated"), "projectPath": installation.get("projectPath"),
                    "mcpServers": servers or None, "errors": errors or None,
                }))
        for plugin in inline_plugins:
            servers = plugin.mcp_servers or await load_plugin_mcp_servers(plugin, [], locations)
            errors = [get_plugin_error_message(e) for e in _errors_for(inline_errors, plugin.source, plugin.name)]
            plugins.append(_defined({
                "id": plugin.source, "version": plugin.manifest.get("version") or "unknown", "scope": "session",
                "enabled": plugin.enabled is not False, "installPath": plugin.path, "mcpServers": servers or None,
                "errors": errors or None,
            }))
        for error in path_failures:
            plugins.append({"id": error["source"], "version": "unknown", "scope": "session", "enabled": False,
                            "installPath": cast(dict[str, Any], error).get("path", ""),
                            "errors": [get_plugin_error_message(error)]})
        if available:
            out(json.dumps({"installed": plugins, "available": await _available_plugins(locations)}, indent=2,
                           ensure_ascii=False))
        else:
            out(json.dumps(plugins, indent=2, ensure_ascii=False))
        return 0
    if not plugin_ids and not inline_plugins and not inline_errors:
        out(f"No plugins installed. Use `{PLUGIN_CLI} install` to install a plugin.")
        return 0
    if plugin_ids:
        out("Installed plugins:\n")
    for plugin_id in plugin_ids:
        load_errors = _errors_for(loaded.errors, plugin_id, parse_plugin_identifier(plugin_id).name)
        for installation in installed["plugins"][plugin_id] or ():
            if load_errors:
                status = f"{CROSS} failed to load"
            else:
                status = f"{TICK} enabled" if plugin_id in enabled_plugins else f"{CROSS} disabled"
            out(f"  {POINTER} {plugin_id}")
            out(f"    Version: {installation.get('version') or 'unknown'}")
            out(f"    Scope: {installation['scope']}")
            out(f"    Status: {status}")
            for error in load_errors:
                out(f"    Error: {get_plugin_error_message(error)}")
            out("")
    if inline_plugins or inline_errors:
        out("Session-only plugins (--plugin-dir):\n")
        for plugin in inline_plugins:
            plugin_errors = _errors_for(inline_errors, plugin.source, plugin.name)
            out(f"  {POINTER} {plugin.source}")
            out(f"    Version: {plugin.manifest.get('version') or 'unknown'}")
            out(f"    Path: {plugin.path}")
            out(f"    Status: {f'{CROSS} loaded with errors' if plugin_errors else f'{TICK} loaded'}")
            for error in plugin_errors:
                out(f"    Error: {get_plugin_error_message(error)}")
            out("")
        for error in path_failures:
            out(f"  {POINTER} {error['source']}: {CROSS} {get_plugin_error_message(error)}\n")
    return 0


def _defined(fields: dict[str, Any]) -> dict[str, Any]:
    """`JSON.stringify` drops `undefined` fields."""
    return {k: v for k, v in fields.items() if v is not None}


async def _available_plugins(locations: SettingsLocations) -> list[dict[str, Any]]:
    """`--available`: every marketplace plugin not installed here; marketplace failures are ignored."""
    available: list[dict[str, Any]] = []
    try:
        config = await load_known_marketplaces_config()
        loaded = await load_marketplaces_with_graceful_degradation(config, locations)
        for marketplace in loaded.marketplaces:
            if marketplace.data is None:
                continue
            for entry in marketplace.data["plugins"]:
                plugin_id = create_plugin_id(entry["name"], marketplace.name)
                if not is_plugin_installed(plugin_id, locations):
                    available.append(_defined({
                        "pluginId": plugin_id, "name": entry["name"], "description": entry.get("description"),
                        "marketplaceName": marketplace.name, "version": entry.get("version"),
                        "source": entry["source"],
                    }))
    except Exception as exc:  # noqa: BLE001 - upstream silently ignores marketplace loading errors
        logger.debug("plugin list --available: %s", exc)
    return available


async def marketplace_add_handler(
    source: str, *, locations: SettingsLocations, sparse: Sequence[str] | None = None, scope: str | None = None,
    cowork: bool = False, out: Writer = _stdout, err: Writer = _stderr,
) -> int:
    """`plugin marketplace add <source> [--sparse …] [--scope …]`: fetched, then declared in settings."""
    if cowork:
        set_use_cowork_plugins(True)
    try:
        parsed = await parse_marketplace_input(source)
        if parsed is None:
            err(f"{CROSS} Invalid marketplace source format. Try: owner/repo, https://..., or ./path")
            return 1
        if "error" in parsed:
            err(f"{CROSS} {parsed['error']}")
            return 1
        chosen = scope or "user"
        if chosen not in VALID_INSTALLABLE_SCOPES:
            err(f"{CROSS} Invalid scope '{chosen}'. Use: user, project, or local")
            return 1
        setting_source = scope_to_setting_source(chosen)
        marketplace_source = dict(parsed)
        if sparse:
            if marketplace_source["source"] not in ("github", "git"):
                err(f"{CROSS} --sparse is only supported for github and git marketplace sources "
                    f"(got: {marketplace_source['source']})")
                return 1
            marketplace_source["sparsePaths"] = list(sparse)
        out("Adding marketplace...")
        result = await add_marketplace_source(marketplace_source, locations, out)
        save_marketplace_to_settings(result.name, {"source": result.resolved_source}, locations, setting_source)
        clear_all_caches()
        if result.already_materialized:
            out(f"{TICK} Marketplace '{result.name}' already on disk — declared in {chosen} settings")
        else:
            out(f"{TICK} Successfully added marketplace: {result.name} (declared in {chosen} settings)")
        return 0
    except Exception as error:  # noqa: BLE001 - upstream reports any failure and exits 1
        return handle_marketplace_error(error, "add marketplace", err)


_SOURCE_LABELS = {"github": ("GitHub", "repo"), "git": ("Git", "url"), "url": ("URL", "url"),
                  "directory": ("Directory", "path"), "file": ("File", "path")}


async def marketplace_list_handler(
    *, as_json: bool = False, cowork: bool = False, out: Writer = _stdout, err: Writer = _stderr
) -> int:
    """`plugin marketplace list [--json]`."""
    if cowork:
        set_use_cowork_plugins(True)
    try:
        config = await load_known_marketplaces_config()
        if as_json:
            listed = []
            for name in sorted(config):
                source = dict(config[name].get("source") or {})
                kind = source.get("source")
                entry: dict[str, Any] = {"name": name, "source": kind}
                if kind in _SOURCE_LABELS:
                    key = _SOURCE_LABELS[kind][1]
                    entry[key] = source.get(key)
                entry["installLocation"] = config[name].get("installLocation")
                listed.append(entry)
            out(json.dumps(listed, indent=2, ensure_ascii=False))
            return 0
        if not config:
            out("No marketplaces configured")
            return 0
        out("Configured marketplaces:\n")
        for name, marketplace in config.items():
            out(f"  {POINTER} {name}")
            source = dict(marketplace.get("source") or {})
            label = _SOURCE_LABELS.get(str(source.get("source")))
            if label is not None:
                out(f"    Source: {label[0]} ({source.get(label[1])})")
            out("")
        return 0
    except Exception as error:  # noqa: BLE001 - upstream reports any failure and exits 1
        return handle_marketplace_error(error, "list marketplaces", err)


async def marketplace_remove_handler(
    name: str, *, locations: SettingsLocations, cowork: bool = False, out: Writer = _stdout, err: Writer = _stderr
) -> int:
    """`plugin marketplace remove <name>`."""
    if cowork:
        set_use_cowork_plugins(True)
    try:
        await remove_marketplace_source(name, locations)
        clear_all_caches()
        out(f"{TICK} Successfully removed marketplace: {name}")
        return 0
    except Exception as error:  # noqa: BLE001 - upstream reports any failure and exits 1
        return handle_marketplace_error(error, "remove marketplace", err)


async def marketplace_update_handler(
    name: str | None, *, cowork: bool = False, out: Writer = _stdout, err: Writer = _stderr
) -> int:
    """`plugin marketplace update [name]`: one marketplace, or all of them."""
    if cowork:
        set_use_cowork_plugins(True)
    try:
        if name:
            out(f"Updating marketplace: {name}...")
            await refresh_marketplace(name, out)
            clear_all_caches()
            out(f"{TICK} Successfully updated marketplace: {name}")
            return 0
        names = list(await load_known_marketplaces_config())
        if not names:
            out("No marketplaces configured")
            return 0
        out(f"Updating {len(names)} marketplace(s)...")
        await refresh_all_marketplaces()
        clear_all_caches()
        out(f"{TICK} Successfully updated {len(names)} marketplace(s)")
        return 0
    except Exception as error:  # noqa: BLE001 - upstream reports any failure and exits 1
        return handle_marketplace_error(error, "update marketplace(s)", err)


def _scope_error(scope: str | None, cowork: bool, err: Writer, allowed: Sequence[str] = VALID_INSTALLABLE_SCOPES,
                 message: str | None = None) -> bool:
    if scope is not None and scope not in allowed:
        err(message or f'Invalid scope "{scope}". Valid scopes: {", ".join(allowed)}')
        return True
    if cowork and scope is not None and scope != "user":
        err("--cowork can only be used with user scope")
        return True
    return False


async def plugin_install_handler(
    plugin: str, *, locations: SettingsLocations, scope: str | None = None, cowork: bool = False,
    out: Writer = _stdout, err: Writer = _stderr,
) -> int:
    """`plugin install <plugin> [--scope user|project|local]` (default `user`)."""
    if cowork:
        set_use_cowork_plugins(True)
    chosen = scope or "user"
    if cowork and chosen != "user":
        err("--cowork can only be used with user scope")
        return 1
    if chosen not in VALID_INSTALLABLE_SCOPES:
        err(f"Invalid scope: {chosen}. Must be one of: {', '.join(VALID_INSTALLABLE_SCOPES)}.")
        return 1
    return await install_plugin(plugin, chosen, locations=locations, out=out, err=err)


async def plugin_uninstall_handler(
    plugin: str, *, locations: SettingsLocations, scope: str | None = None, keep_data: bool = False,
    cowork: bool = False, out: Writer = _stdout, err: Writer = _stderr,
) -> int:
    """`plugin uninstall <plugin> [--scope …] [--keep-data]`."""
    if cowork:
        set_use_cowork_plugins(True)
    chosen = scope or "user"
    if cowork and chosen != "user":
        err("--cowork can only be used with user scope")
        return 1
    if chosen not in VALID_INSTALLABLE_SCOPES:
        err(f"Invalid scope: {chosen}. Must be one of: {', '.join(VALID_INSTALLABLE_SCOPES)}.")
        return 1
    return await uninstall_plugin(plugin, chosen, keep_data, locations=locations, out=out,
                                  err=err)


async def plugin_enable_handler(
    plugin: str, *, locations: SettingsLocations, scope: str | None = None, cowork: bool = False,
    out: Writer = _stdout, err: Writer = _stderr,
) -> int:
    """`plugin enable <plugin> [--scope …]` (scope auto-detected when omitted)."""
    if cowork:
        set_use_cowork_plugins(True)
    if _scope_error(scope, cowork, err):
        return 1
    chosen = scope if scope is not None else ("user" if cowork else None)
    return await enable_plugin(plugin, cast(InstallableScope | None, chosen), locations=locations, out=out, err=err)


async def plugin_disable_handler(
    plugin: str | None, *, locations: SettingsLocations, scope: str | None = None, all_plugins: bool = False,
    cowork: bool = False, out: Writer = _stdout, err: Writer = _stderr,
) -> int:
    """`plugin disable [plugin] [--all] [--scope …]`."""
    if all_plugins and plugin:
        err("Cannot use --all with a specific plugin")
        return 1
    if not all_plugins and not plugin:
        err("Please specify a plugin name or use --all to disable all plugins")
        return 1
    if cowork:
        set_use_cowork_plugins(True)
    if all_plugins:
        if scope:
            err("Cannot use --scope with --all")
            return 1
        return await disable_all_plugins(locations=locations, out=out, err=err)
    if _scope_error(scope, cowork, err):
        return 1
    chosen = scope if scope is not None else ("user" if cowork else None)
    return await disable_plugin(cast(str, plugin), cast(InstallableScope | None, chosen), locations=locations, out=out,
                                err=err)


async def plugin_update_handler(
    plugin: str, *, locations: SettingsLocations, scope: str | None = None, cowork: bool = False,
    out: Writer = _stdout, err: Writer = _stderr,
) -> int:
    """`plugin update <plugin> [--scope user|project|local|managed]` (default `user`)."""
    if cowork:
        set_use_cowork_plugins(True)
    chosen = scope or "user"
    if scope is not None and scope not in VALID_UPDATE_SCOPES:
        err(f'Invalid scope "{scope}". Valid scopes: {", ".join(VALID_UPDATE_SCOPES)}')
        return 1
    if cowork and chosen != "user":
        err("--cowork can only be used with user scope")
        return 1
    return await update_plugin_cli(plugin, chosen, locations=locations, out=out, err=err)


__all__ = [
    "handle_marketplace_error",
    "marketplace_add_handler",
    "marketplace_list_handler",
    "marketplace_remove_handler",
    "marketplace_update_handler",
    "plugin_disable_handler",
    "plugin_enable_handler",
    "plugin_install_handler",
    "plugin_list_handler",
    "plugin_uninstall_handler",
    "plugin_update_handler",
    "plugin_validate_handler",
    "print_validation_result",
]
