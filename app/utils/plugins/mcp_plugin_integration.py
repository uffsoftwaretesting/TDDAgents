"""
The enabled plugins' MCP servers: read from each plugin, their variables resolved, scoped.

Ported from `reference/claude-code/src/utils/plugins/mcpPluginIntegration.ts` ->
`loadMcpServersFromMcpb`, `loadPluginMcpServers`, `loadMcpServersFromFile`,
`UnconfiguredChannel`, `getUnconfiguredChannels`, `loadChannelUserConfig`,
`addPluginScopeToServers`, `extractMcpServersFromPlugins`, `buildMcpUserConfig`,
`resolvePluginMcpEnvironment`, `getPluginMcpServers`.

A plugin's servers come from its `.mcp.json` (lowest), then the manifest's `mcpServers` (a JSON
file path, an MCPB bundle, inline configs, or a list of those, the last one winning). They are
kept unresolved on the plugin (`LoadedPlugin.mcp_servers`); `get_plugin_mcp_servers` resolves
them on each use — `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PLUGIN_DATA}`, then `${user_config.X}`, then
`${VAR}` from the environment — and names each `plugin:<plugin>:<server>`, scope `dynamic`,
`plugin_source` the plugin id. A stdio server also gets `CLAUDE_PLUGIN_ROOT` / `CLAUDE_PLUGIN_DATA`
in its environment (the plugin format's names, kept verbatim). `services/mcp/config.py` merges
them below the user's servers (`getClaudeCodeMcpConfigs`).

`locations` is the run's settings, where the saved plugin options and channel configs are read.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.mcp.env_expansion import expand_env_vars_in_string
from app.services.mcp.types import (
    McpServerConfig,
    adheres_to_server_schema,
    server_config_from_raw,
)
from app.types.plugin import LoadedPlugin, PluginError
from app.utils.plugins.mcpb_handler import (
    McpbNeedsConfigResult,
    UserConfigSchema,
    UserConfigValues,
    is_mcpb_source,
    load_mcp_server_user_config,
    load_mcpb_file,
    validate_user_config,
)
from app.utils.plugins.plugin_directories import get_plugin_data_dir
from app.utils.plugins.plugin_options_storage import (
    get_plugin_storage_id,
    load_plugin_options,
    substitute_plugin_variables,
    substitute_user_config_variables,
)
from app.utils.settings.settings import SettingsLocations

logger = logging.getLogger("TDDOrchestrator.Plugins")

RawServers = dict[str, dict[str, Any]]


def _error_text(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


async def load_mcp_servers_from_mcpb(
    plugin: LoadedPlugin, mcpb_path: str, errors: list[PluginError], locations: SettingsLocations
) -> RawServers | None:
    """
    `loadMcpServersFromMcpb`: the bundle's server, named after its manifest; `None` when it still
    needs its user configuration (not an error) or fails (the error recorded by kind).
    """
    try:
        result = await load_mcpb_file(mcpb_path, plugin.path, plugin.repository, locations,
                                      lambda status: logger.debug("MCPB [%s]: %s", plugin.name, status))
        if isinstance(result, McpbNeedsConfigResult):
            logger.debug("MCPB %s requires user configuration.", mcpb_path)
            return None
        name = str(result.manifest["name"])
        logger.debug('Loaded MCP server "%s" from MCPB (extracted to %s)', name, result.extracted_path)
        return {name: dict(result.mcp_config)}
    except Exception as exc:  # noqa: BLE001 - upstream classifies any failure into a plugin error
        message = _error_text(exc)
        logger.error("Failed to load MCPB %s: %s", mcpb_path, message)
        source = f"{plugin.name}@{plugin.repository}"
        if mcpb_path.startswith("http") and ("download" in message or "network" in message):
            errors.append({"type": "mcpb-download-failed", "source": source, "plugin": plugin.name, "url": mcpb_path,
                           "reason": message})
        elif "manifest" in message or "user configuration" in message:
            errors.append({"type": "mcpb-invalid-manifest", "source": source, "plugin": plugin.name,
                           "mcpbPath": mcpb_path, "validationError": message})
        else:
            errors.append({"type": "mcpb-extract-failed", "source": source, "plugin": plugin.name,
                           "mcpbPath": mcpb_path, "reason": message})
        return None


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


async def load_mcp_servers_from_file(plugin_path: str, relative_path: str) -> RawServers | None:
    """
    `loadMcpServersFromFile`: a JSON file of servers (a `.mcp.json` with `mcpServers`, or the bare
    map); each server that fails the schema is logged and skipped. `None` when there is no file.
    """
    file_path = os.path.normpath(os.path.join(plugin_path, relative_path))
    try:
        content = _read_text(file_path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.error("Failed to load MCP servers from %s: %s", file_path, exc)
        return None
    try:
        parsed = json.loads(content)
        servers = (parsed.get("mcpServers") if isinstance(parsed, dict) else None) or parsed
        validated: RawServers = {}
        for name, config in (servers.items() if isinstance(servers, dict) else ()):
            if adheres_to_server_schema(config):
                validated[str(name)] = dict(config)
            else:
                logger.error("Invalid MCP server config for %s in %s", name, file_path)
        return validated
    except ValueError as exc:
        logger.error("Failed to load MCP servers from %s: %s", file_path, exc)
        return None


async def _load_spec(
    plugin: LoadedPlugin, spec: Any, errors: list[PluginError], locations: SettingsLocations
) -> RawServers | None:
    if isinstance(spec, str):
        if is_mcpb_source(spec):
            return await load_mcp_servers_from_mcpb(plugin, spec, errors, locations)
        return await load_mcp_servers_from_file(plugin.path, spec)
    return {str(k): dict(v) for k, v in spec.items()} if isinstance(spec, Mapping) else None


async def load_plugin_mcp_servers(
    plugin: LoadedPlugin, errors: list[PluginError], locations: SettingsLocations
) -> RawServers | None:
    """
    `loadPluginMcpServers`: the plugin's `.mcp.json`, then its manifest's `mcpServers` (a path, an
    MCPB, inline configs, or a list of them in order); `None` when it declares none.
    """
    servers: RawServers = {}
    default = await load_mcp_servers_from_file(plugin.path, ".mcp.json")
    if default:
        servers.update(default)
    spec = plugin.manifest.get("mcpServers")
    if spec:
        specs = spec if isinstance(spec, list) else [spec]
        for one in specs:
            try:
                loaded = await _load_spec(plugin, one, errors, locations)
            except Exception as exc:  # noqa: BLE001 - one bad spec must not lose the others
                logger.error("Failed to load MCP servers from spec for plugin %s: %s", plugin.name, exc)
                loaded = None
            if loaded:
                servers.update(loaded)
    return servers or None


@dataclass(frozen=True)
class UnconfiguredChannel:
    """`UnconfiguredChannel`."""

    server: str
    display_name: str
    config_schema: UserConfigSchema


def get_unconfigured_channels(plugin: LoadedPlugin, locations: SettingsLocations) -> list[UnconfiguredChannel]:
    """`getUnconfiguredChannels`: the manifest's channels whose required `userConfig` is not saved."""
    unconfigured: list[UnconfiguredChannel] = []
    for channel in plugin.manifest.get("channels") or ():
        schema = channel.get("userConfig")
        if not schema:
            continue
        saved = load_mcp_server_user_config(plugin.repository, channel["server"], locations) or {}
        if not validate_user_config(saved, schema).valid:
            unconfigured.append(UnconfiguredChannel(
                server=channel["server"], display_name=channel.get("displayName") or channel["server"],
                config_schema=schema))
    return unconfigured


def _load_channel_user_config(
    plugin: LoadedPlugin, server_name: str, locations: SettingsLocations
) -> UserConfigValues | None:
    """`loadChannelUserConfig`."""
    channel = next((c for c in plugin.manifest.get("channels") or () if c.get("server") == server_name), None)
    if channel is None or not channel.get("userConfig"):
        return None
    return load_mcp_server_user_config(plugin.repository, server_name, locations)


def build_mcp_user_config(
    plugin: LoadedPlugin, server_name: str, locations: SettingsLocations
) -> UserConfigValues | None:
    """`buildMcpUserConfig`: the plugin's options, the channel's own on top; `None` when neither exists."""
    top_level = (load_plugin_options(get_plugin_storage_id(plugin), locations)
                 if plugin.manifest.get("userConfig") else None)
    channel = _load_channel_user_config(plugin, server_name, locations)
    if top_level is None and channel is None:
        return None
    return {**(top_level or {}), **(channel or {})}


def resolve_plugin_mcp_environment(
    config: Mapping[str, Any],
    plugin: LoadedPlugin,
    user_config: Mapping[str, Any] | None = None,
    errors: list[PluginError] | None = None,
    plugin_name: str | None = None,
    server_name: str | None = None,
) -> dict[str, Any]:
    """
    `resolvePluginMcpEnvironment`: plugin variables, then user config, then the environment, over
    a stdio server's command, args and env (plus `CLAUDE_PLUGIN_ROOT` / `CLAUDE_PLUGIN_DATA`) or a
    remote server's url and headers. Missing environment variables become an `mcp-config-invalid`.
    """
    missing: list[str] = []

    def resolve(value: str) -> str:
        resolved = substitute_plugin_variables(value, plugin)
        if user_config is not None:
            resolved = substitute_user_config_variables(resolved, user_config)
        expanded, absent = expand_env_vars_in_string(resolved)
        missing.extend(absent)
        return expanded

    resolved_config = dict(config)
    kind = config.get("type")
    if kind in (None, "stdio"):
        if resolved_config.get("command"):
            resolved_config["command"] = resolve(str(resolved_config["command"]))
        if resolved_config.get("args"):
            resolved_config["args"] = [resolve(str(a)) for a in resolved_config["args"]]
        env: dict[str, str] = {
            "CLAUDE_PLUGIN_ROOT": plugin.path, "CLAUDE_PLUGIN_DATA": get_plugin_data_dir(plugin.source),
            **dict(resolved_config.get("env") or {}),
        }
        resolved_config["env"] = {
            key: value if key in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA") else resolve(value)
            for key, value in env.items()
        }
    elif kind in ("sse", "http", "ws"):
        if resolved_config.get("url"):
            resolved_config["url"] = resolve(str(resolved_config["url"]))
        if resolved_config.get("headers"):
            resolved_config["headers"] = {k: resolve(str(v)) for k, v in resolved_config["headers"].items()}
    if errors is not None and missing:
        names = ", ".join(dict.fromkeys(missing))
        logger.warning("Missing environment variables in plugin MCP config: %s", names)
        if plugin_name and server_name:
            errors.append({"type": "mcp-config-invalid", "source": f"plugin:{plugin_name}", "plugin": plugin_name,
                           "serverName": server_name, "validationError": f"Missing environment variables: {names}"})
    return resolved_config


def add_plugin_scope_to_servers(
    servers: Mapping[str, Mapping[str, Any]], plugin_name: str, plugin_source: str
) -> dict[str, McpServerConfig]:
    """`addPluginScopeToServers`: `plugin:<plugin>:<server>`, scope `dynamic`, its `plugin_source`."""
    scoped: dict[str, McpServerConfig] = {}
    for name, config in servers.items():
        base = server_config_from_raw(dict(config), "dynamic")
        scoped[f"plugin:{plugin_name}:{name}"] = McpServerConfig(
            type=base.type, scope=base.scope, command=base.command, args=base.args, env=base.env, url=base.url,
            headers=base.headers, headers_helper=base.headers_helper, oauth=base.oauth, plugin_source=plugin_source)
    return scoped


def _resolve_all(
    plugin: LoadedPlugin, servers: RawServers, errors: list[PluginError], locations: SettingsLocations
) -> dict[str, dict[str, Any]]:
    """Each server resolved; one whose `${user_config.X}` is missing becomes a `generic-error`."""
    resolved: dict[str, dict[str, Any]] = {}
    for name, config in servers.items():
        user_config = build_mcp_user_config(plugin, name, locations)
        try:
            resolved[name] = resolve_plugin_mcp_environment(config, plugin, user_config, errors, plugin.name, name)
        except Exception as exc:  # noqa: BLE001 - one bad server must not lose the plugin's others
            errors.append({"type": "generic-error", "source": name, "plugin": plugin.name, "error": _error_text(exc)})
    return resolved


async def extract_mcp_servers_from_plugins(
    plugins: list[LoadedPlugin], errors: list[PluginError], locations: SettingsLocations
) -> dict[str, McpServerConfig]:
    """`extractMcpServersFromPlugins`: every enabled plugin's servers, resolved and scoped (cached unresolved)."""
    all_servers: dict[str, McpServerConfig] = {}
    for plugin in plugins:
        if not plugin.enabled:
            continue
        servers = await load_plugin_mcp_servers(plugin, errors, locations)
        if not servers:
            continue
        resolved = _resolve_all(plugin, servers, errors, locations)
        plugin.mcp_servers = servers
        logger.debug("Loaded %d MCP servers from plugin %s", len(servers), plugin.name)
        all_servers.update(add_plugin_scope_to_servers(resolved, plugin.name, plugin.source))
    return all_servers


async def get_plugin_mcp_servers(
    plugin: LoadedPlugin, errors: list[PluginError], locations: SettingsLocations
) -> dict[str, McpServerConfig] | None:
    """`getPluginMcpServers`: one enabled plugin's servers (its cached ones first), resolved and scoped."""
    if not plugin.enabled:
        return None
    servers = plugin.mcp_servers or await load_plugin_mcp_servers(plugin, errors, locations)
    if not servers:
        return None
    return add_plugin_scope_to_servers(_resolve_all(plugin, servers, errors, locations), plugin.name, plugin.source)


__all__ = [
    "UnconfiguredChannel",
    "add_plugin_scope_to_servers",
    "build_mcp_user_config",
    "extract_mcp_servers_from_plugins",
    "get_plugin_mcp_servers",
    "get_unconfigured_channels",
    "load_mcp_servers_from_file",
    "load_mcp_servers_from_mcpb",
    "load_plugin_mcp_servers",
    "resolve_plugin_mcp_environment",
]
