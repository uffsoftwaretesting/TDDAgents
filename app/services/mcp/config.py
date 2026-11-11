"""
Where a run's MCP servers come from (Phase 8).

Ported from `reference/claude-code/src/services/mcp/config.ts` -> `parseMcpConfig`,
`parseMcpConfigFromFilePath`, `expandEnvVars`, `getMcpConfigsByScope`,
`getClaudeCodeMcpConfigs`, `getEnterpriseMcpFilePath`, `doesEnterpriseMcpConfigExist`,
`isMcpServerDenied` / `isMcpServerAllowedByPolicy`, `isMcpServerDisabled`, `unwrapCcrProxyUrl`,
`getMcpServerSignature`, `dedupPluginMcpServers`; and `services/mcp/utils.ts` ->
`getProjectMcpServerStatus`.

Scopes, lowest to highest precedence: user (`mcpServers` in the global config file), project
(every `.mcp.json` from the filesystem root down to the cwd, nearer files winning), local (the
global config's `projects[<project key>].mcpServers`). An enterprise `managed-mcp.json` is
exclusive: when it exists, it is the only source. The global config file mirrors upstream's
`~/.claude.json` as `~/.tddagents.json` (`$TDDAGENTS_CONFIG_DIR/.tddagents.json` when set,
as `CLAUDE_CONFIG_DIR` does for `getGlobalClaudeFile`). The project key is the cwd's git root,
else the cwd (`getProjectPathForConfig`).

Project servers need approval (`get_project_mcp_server_status`), and only the approved ones reach
the run. A non-interactive session (`-p`, the SDK) approves every one `disabledMcpjsonServers`
does not name, exactly as `claude -p`; the REPL asks about the pending ones after the trust
dialog (`app/services/mcp_server_approval.py`) and leaves an unanswered one out.

The enabled plugins' servers (`app/utils/plugins/mcp_plugin_integration.py`, read through
`load_all_plugins_cache_only`) sit below every other scope, and are kept even when the policy
restricts MCP to plugins (`strictPluginOnlyCustomization`). A plugin server that would launch
the same process or reach the same URL as an enabled manual server, or an earlier plugin's, is
dropped (`dedupPluginMcpServers`). `get_claude_code_mcp_configs` is therefore async, as
upstream's. The plugin MCP errors, upstream's second result, are logged here (the run reads
the config errors only).

Not ported: claude.ai connectors, dynamic `--mcp-config` servers, the Windows
`npx` warning, and writing configs (`addMcpConfig` / `removeMcpConfig`).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from app.utils.debug import log_for_debugging
from app.services.mcp.env_expansion import expand_env_vars_in_string
from app.services.mcp.normalization import normalize_name_for_mcp
from app.services.mcp.types import (
    McpServerConfig,
    ValidationError,
    adheres_to_server_schema,
    server_config_from_raw,
)
from app.types.permissions import PermissionRuleSource
from app.utils.hooks.hooks_config_snapshot import is_restricted_to_plugin_only
from app.utils.settings.settings import (
    SettingsLocations,
    get_settings_for_source,
    has_skip_dangerous_mode_permission_prompt,
    load_settings,
)

logger = logging.getLogger(__name__)

GLOBAL_CONFIG_FILENAME = ".tddagents.json"
MCP_JSON_FILENAME = ".mcp.json"
#: `getEnterpriseMcpFilePath`: beside the managed settings.
MANAGED_MCP_FILENAME = "managed-mcp.json"

SCHEMA_ERROR = "Does not adhere to MCP server configuration schema"


def get_global_config_path() -> Path:
    """`getGlobalClaudeFile`, under this project's name."""
    base = os.environ.get("TDDAGENTS_CONFIG_DIR")
    return (Path(base) if base else Path.home()) / GLOBAL_CONFIG_FILENAME


def get_project_config_key(cwd: str | Path) -> str:
    """`getProjectPathForConfig`: the git root above `cwd`, else `cwd`, resolved."""
    start = Path(cwd).resolve()
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return str(directory)
    return str(start)


def _read_global_config() -> dict[str, Any]:
    try:
        data = json.loads(get_global_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _project_config(cwd: str | Path) -> Mapping[str, Any]:
    projects = _read_global_config().get("projects")
    entry = projects.get(get_project_config_key(cwd)) if isinstance(projects, dict) else None
    return entry if isinstance(entry, dict) else {}


def _expand(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """`expandEnvVars`: stdio command/args/env, remote url/headers; other types untouched."""
    missing: list[str] = []

    def expand(value: str) -> str:
        expanded, vars_ = expand_env_vars_in_string(value)
        missing.extend(vars_)
        return expanded

    out = dict(raw)
    kind = raw.get("type")
    if kind in (None, "stdio"):
        out["command"] = expand(raw["command"])
        out["args"] = [expand(a) for a in raw.get("args", [])]
        if isinstance(raw.get("env"), dict):
            out["env"] = {k: expand(v) for k, v in raw["env"].items()}
    elif kind in ("sse", "http", "ws"):
        out["url"] = expand(raw["url"])
        if isinstance(raw.get("headers"), dict):
            out["headers"] = {k: expand(v) for k, v in raw["headers"].items()}
    return out, list(dict.fromkeys(missing))


def parse_mcp_config(
    config_object: Any, *, expand_vars: bool, scope: str, file_path: str | None = None
) -> tuple[dict[str, McpServerConfig] | None, list[ValidationError]]:
    """`parseMcpConfig`: one invalid server rejects the whole document, as zod's union does."""
    servers = config_object.get("mcpServers") if isinstance(config_object, dict) else None
    if not isinstance(servers, dict):
        return None, [ValidationError(path="mcpServers", message=SCHEMA_ERROR, scope=scope, severity="fatal",
                                      file=file_path)]
    failures = [name for name, raw in servers.items() if not adheres_to_server_schema(raw)]
    if failures:
        return None, [
            ValidationError(path=f"mcpServers.{name}", message=SCHEMA_ERROR, scope=scope, severity="fatal",
                            file=file_path)
            for name in failures
        ]
    errors: list[ValidationError] = []
    validated: dict[str, McpServerConfig] = {}
    for name, raw in servers.items():
        entry = dict(raw)
        if expand_vars:
            entry, missing = _expand(entry)
            if missing:
                names = ", ".join(missing)
                errors.append(ValidationError(
                    path=f"mcpServers.{name}",
                    message=f"Missing environment variables: {names}",
                    scope=scope,
                    severity="warning",
                    file=file_path,
                    suggestion=f"Set the following environment variables: {names}",
                    server_name=name,
                ))
        validated[name] = server_config_from_raw(entry, scope)
    return validated, errors


def parse_mcp_config_from_file_path(
    file_path: str | Path, *, expand_vars: bool, scope: str
) -> tuple[dict[str, McpServerConfig] | None, list[ValidationError]]:
    """`parseMcpConfigFromFilePath`."""
    path = str(file_path)
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, [ValidationError(path="", message=f"MCP config file not found: {path}", scope=scope,
                                      severity="fatal", file=path,
                                      suggestion="Check that the file path is correct")]
    except OSError as exc:
        return None, [ValidationError(path="", message=f"Failed to read file: {exc}", scope=scope,
                                      severity="fatal", file=path,
                                      suggestion="Check file permissions and ensure the file exists")]
    try:
        parsed = json.loads(content)
    except ValueError:
        return None, [ValidationError(path="", message="MCP config is not a valid JSON", scope=scope,
                                      severity="fatal", file=path,
                                      suggestion="Fix the JSON syntax errors in the file")]
    return parse_mcp_config(parsed, expand_vars=expand_vars, scope=scope, file_path=path)


def _file_scope(path: Path, scope: str) -> tuple[dict[str, McpServerConfig], list[ValidationError]]:
    config, errors = parse_mcp_config_from_file_path(path, expand_vars=True, scope=scope)
    if config is None:
        real = [e for e in errors if not e.message.startswith("MCP config file not found")]
        if real:
            # `config.ts`: `logForDebugging(`MCP config errors for ${path}: …`, { level: 'error' })`, the
            # messages JSON-encoded: the debug log, never the terminal (2026-11-10).
            messages = json.dumps([e.message for e in real])
            log_for_debugging(f"MCP config errors for {path}: {messages}", level="error")
        return {}, real
    return config, errors


def get_mcp_configs_by_scope(
    scope: Literal["project", "user", "local", "enterprise"], cwd: str | Path, locations: SettingsLocations
) -> tuple[dict[str, McpServerConfig], list[ValidationError]]:
    """`getMcpConfigsByScope`."""
    if scope == "project":
        start = Path(cwd).resolve()
        servers: dict[str, McpServerConfig] = {}
        errors: list[ValidationError] = []
        for directory in reversed((start, *start.parents[:-1])):
            found, errs = _file_scope(directory / MCP_JSON_FILENAME, "project")
            servers.update(found)
            errors.extend(errs)
        return servers, errors
    if scope == "enterprise":
        return _file_scope(Path(locations.managed_dir) / MANAGED_MCP_FILENAME, "enterprise")
    source = _read_global_config() if scope == "user" else _project_config(cwd)
    mcp_servers = source.get("mcpServers")
    if not mcp_servers:
        return {}, []
    config, errors = parse_mcp_config({"mcpServers": mcp_servers}, expand_vars=True, scope=scope)
    return config or {}, errors


def get_project_mcp_server_status(
    name: str, locations: SettingsLocations, *, is_non_interactive_session: bool
) -> Literal["approved", "rejected", "pending"]:
    """
    `getProjectMcpServerStatus`: a `.mcp.json` server's approval, from the merged settings.
    Rejected when `disabledMcpjsonServers` names it; approved when `enabledMcpjsonServers` names
    it or `enableAllProjectMcpServers` is set (names compared normalized, `normalizeNameForMCP`),
    when a trusted source accepted the bypass-permissions dialog, or when the session is
    non-interactive (`-p`, the SDK: nobody can be asked). Otherwise it is pending, asked after the
    trust dialog (`app/services/mcp_server_approval.py`). Upstream's
    `isSettingSourceEnabled('projectSettings')` conjunct always holds here: every settings source
    is enabled (there is no `--setting-sources`).
    """
    settings = load_settings(locations)
    normalized = normalize_name_for_mcp(name)

    def lists(key: str) -> bool:
        listed = settings.get(key)
        return isinstance(listed, list) and any(normalize_name_for_mcp(str(n)) == normalized for n in listed)

    if lists("disabledMcpjsonServers"):
        return "rejected"
    if lists("enabledMcpjsonServers") or settings.get("enableAllProjectMcpServers"):
        return "approved"
    # Under bypass permissions there is no approval popup: approved once a trusted source (never
    # the project's own settings) has accepted the bypass dialog.
    if has_skip_dangerous_mode_permission_prompt(locations):
        return "approved"
    # Nobody to ask in a non-interactive session (`claude -p`, the SDK).
    if is_non_interactive_session:
        return "approved"
    return "pending"


def is_mcp_server_disabled(name: str, cwd: str | Path) -> bool:
    """`isMcpServerDisabled`: the project's `disabledMcpServers` in the global config."""
    return name in (_project_config(cwd).get("disabledMcpServers") or [])


def set_mcp_server_enabled(name: str, enabled: bool, cwd: str | Path) -> None:
    """
    `setMcpServerEnabled` (`saveCurrentProjectConfig`): the server leaves or joins the project's
    `disabledMcpServers`. Upstream's default-disabled built-ins (`enabledMcpServers`) do not exist here.
    """
    data = _read_global_config()
    raw_projects = data.get("projects")
    projects: dict[str, Any] = dict(raw_projects) if isinstance(raw_projects, dict) else {}
    key = get_project_config_key(cwd)
    entry = dict(projects.get(key) or {})
    previous = list(entry.get("disabledMcpServers") or [])
    if enabled:
        updated = [n for n in previous if n != name]
    else:
        updated = previous if name in previous else [*previous, name]
    if updated == previous:
        return
    entry["disabledMcpServers"] = updated
    data["projects"] = {**projects, key: entry}
    path = get_global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def filter_mcp_servers_by_policy(
    servers: Mapping[str, McpServerConfig], locations: SettingsLocations
) -> tuple[dict[str, McpServerConfig], list[str]]:
    """`filterMcpServersByPolicy`: the servers the policy allows, and the names it blocks."""
    merged = load_settings(locations)
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations) or {}
    allowed: dict[str, McpServerConfig] = {}
    blocked: list[str] = []
    for name, config in servers.items():
        if is_mcp_server_allowed_by_policy(name, config, merged, policy):
            allowed[name] = config
        else:
            blocked.append(name)
    return allowed, blocked


def _command_array(config: McpServerConfig) -> list[str] | None:
    if config.transport != "stdio" or not config.command:
        return None
    return [config.command, *config.args]


def _url(config: McpServerConfig) -> str | None:
    return config.url if config.transport in ("sse", "http", "ws", "sse-ide", "ws-ide", "claudeai-proxy") else None


def _url_matches(url: str, pattern: str) -> bool:
    """`urlMatchesPattern`: `*` is the only wildcard."""
    regex = "^" + ".*".join(re.escape(part) for part in pattern.split("*")) + "$"
    return re.match(regex, url) is not None


def _is_name(entry: Any) -> bool:
    return isinstance(entry, dict) and "serverName" in entry


def _is_command(entry: Any) -> bool:
    return isinstance(entry, dict) and "serverCommand" in entry


def _is_url(entry: Any) -> bool:
    return isinstance(entry, dict) and "serverUrl" in entry


def _denied(name: str, config: McpServerConfig, settings: Mapping[str, Any]) -> bool:
    """`isMcpServerDenied`: denylists merge from every source."""
    entries = settings.get("deniedMcpServers") or []
    if any(_is_name(e) and e["serverName"] == name for e in entries):
        return True
    command, url = _command_array(config), _url(config)
    if command is not None and any(_is_command(e) and list(e["serverCommand"]) == command for e in entries):
        return True
    return url is not None and any(_is_url(e) and _url_matches(url, str(e["serverUrl"])) for e in entries)


def is_mcp_server_allowed_by_policy(
    name: str, config: McpServerConfig, merged: Mapping[str, Any], policy: Mapping[str, Any]
) -> bool:
    """`isMcpServerAllowedByPolicy`; the allowlist comes from policy alone under `allowManagedMcpServersOnly`."""
    if _denied(name, config, merged):
        return False
    allow_source = policy if policy.get("allowManagedMcpServersOnly") is True else merged
    allowed = allow_source.get("allowedMcpServers")
    if allowed is None:
        return True
    if not allowed:
        return False
    by_name = any(_is_name(e) and e["serverName"] == name for e in allowed)
    command, url = _command_array(config), _url(config)
    if command is not None:
        if any(_is_command(e) for e in allowed):
            return any(_is_command(e) and list(e["serverCommand"]) == command for e in allowed)
        return by_name
    if url is not None:
        if any(_is_url(e) for e in allowed):
            return any(_is_url(e) and _url_matches(url, str(e["serverUrl"])) for e in allowed)
        return by_name
    return by_name


def get_mcp_config_by_name(name: str, cwd: str | Path, locations: SettingsLocations) -> McpServerConfig | None:
    """
    `getMcpConfigByName`: a configured server by name, enterprise first, then local, project and user
    (only enterprise when MCP is restricted to plugins).
    """
    from app.utils.hooks.hooks_config_snapshot import is_restricted_to_plugin_only

    enterprise, _ = get_mcp_configs_by_scope("enterprise", cwd, locations)
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations)
    if is_restricted_to_plugin_only(policy, "mcp"):
        return enterprise.get(name)
    if name in enterprise:
        return enterprise[name]
    for scope in ("local", "project", "user"):
        servers, _ = get_mcp_configs_by_scope(scope, cwd, locations)
        if name in servers:
            return servers[name]
    return None


#: `CCR_PROXY_PATH_MARKERS`.
CCR_PROXY_PATH_MARKERS = ("/v2/session_ingress/shttp/mcp/", "/v2/ccr-sessions/")
#: The plugin load errors upstream logs as MCP errors (`getClaudeCodeMcpConfigs`).
_MCP_PLUGIN_ERROR_TYPES = ("mcp-config-invalid", "mcpb-download-failed", "mcpb-extract-failed",
                           "mcpb-invalid-manifest")


def unwrap_ccr_proxy_url(url: str) -> str:
    """`unwrapCcrProxyUrl`: a session-ingress proxy URL's original `mcp_url`, else the URL itself."""
    if not any(marker in url for marker in CCR_PROXY_PATH_MARKERS):
        return url
    try:
        original = parse_qs(urlparse(url).query).get("mcp_url")
    except ValueError:
        return url
    return original[0] if original and original[0] else url


def get_mcp_server_signature(config: McpServerConfig) -> str | None:
    """`getMcpServerSignature`: `stdio:<command array>` or `url:<url>`; `None` for neither (sdk)."""
    if config.type in (None, "stdio"):
        return "stdio:" + json.dumps([config.command, *config.args], separators=(",", ":"))
    if config.url:
        return f"url:{unwrap_ccr_proxy_url(config.url)}"
    return None


def dedup_plugin_mcp_servers(
    plugin_servers: Mapping[str, McpServerConfig], manual_servers: Mapping[str, McpServerConfig]
) -> tuple[dict[str, McpServerConfig], list[tuple[str, str]]]:
    """
    `dedupPluginMcpServers`: drop each plugin server whose signature matches a manual server
    (manual wins) or an earlier plugin's (first wins). Returns the kept servers and the
    `(name, duplicateOf)` pairs suppressed.
    """
    manual_sigs: dict[str, str] = {}
    for name, config in manual_servers.items():
        sig = get_mcp_server_signature(config)
        if sig is not None and sig not in manual_sigs:
            manual_sigs[sig] = name
    servers: dict[str, McpServerConfig] = {}
    suppressed: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    for name, config in plugin_servers.items():
        sig = get_mcp_server_signature(config)
        if sig is None:
            servers[name] = config
            continue
        duplicate = manual_sigs.get(sig)
        if duplicate is None:
            duplicate = seen.get(sig)
        if duplicate is not None:
            logger.debug('Suppressing plugin MCP server "%s": duplicates "%s"', name, duplicate)
            suppressed.append((name, duplicate))
            continue
        seen[sig] = name
        servers[name] = config
    return servers, suppressed


async def _plugin_mcp_servers(locations: SettingsLocations) -> dict[str, McpServerConfig]:
    """The enabled plugins' servers (`getPluginMcpServers` for each), their load errors logged."""
    from app.types.plugin import PluginError, get_plugin_error_message
    from app.utils.plugins.mcp_plugin_integration import get_plugin_mcp_servers
    from app.utils.plugins.plugin_loader import load_all_plugins_cache_only

    result = await load_all_plugins_cache_only(locations)
    for error in result.errors:
        if error["type"] in _MCP_PLUGIN_ERROR_TYPES:
            logger.error("Plugin MCP loading error - %s: %s", error["type"], get_plugin_error_message(error))
        else:
            logger.debug("Plugin not available for MCP: %s - error type: %s", error["source"], error["type"])
    mcp_errors: list[PluginError] = []
    servers: dict[str, McpServerConfig] = {}
    for plugin in result.enabled:
        found = await get_plugin_mcp_servers(plugin, mcp_errors, locations)
        if found:
            servers.update(found)
    for error in mcp_errors:
        logger.error("Plugin MCP server error - %s: %s", error["type"], get_plugin_error_message(error))
    return servers


async def get_claude_code_mcp_configs(
    cwd: str | Path, locations: SettingsLocations, dynamic_servers: Mapping[str, McpServerConfig] | None = None,
    *, is_non_interactive_session: bool,
) -> tuple[dict[str, McpServerConfig], list[ValidationError]]:
    """
    `getClaudeCodeMcpConfigs`: the run's servers — plugin < user < approved project < local —
    deduplicated, approved and filtered by policy. `dynamic_servers` only count as dedup targets.
    `is_non_interactive_session` is upstream's `getIsNonInteractiveSession()`: it decides whether
    a project server nobody has answered for is approved.
    """
    merged = load_settings(locations)
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations) or {}
    enterprise, enterprise_errors = get_mcp_configs_by_scope("enterprise", cwd, locations)
    if (Path(locations.managed_dir) / MANAGED_MCP_FILENAME).is_file() and not enterprise_errors:
        return {n: c for n, c in enterprise.items() if is_mcp_server_allowed_by_policy(n, c, merged, policy)}, []
    locked = is_restricted_to_plugin_only(policy, "mcp")
    none: tuple[dict[str, McpServerConfig], list[ValidationError]] = ({}, [])
    user, user_errors = none if locked else get_mcp_configs_by_scope("user", cwd, locations)
    project, project_errors = none if locked else get_mcp_configs_by_scope("project", cwd, locations)
    local, local_errors = none if locked else get_mcp_configs_by_scope("local", cwd, locations)
    plugin_servers = await _plugin_mcp_servers(locations)
    approved = {n: c for n, c in project.items() if get_project_mcp_server_status(
        n, locations, is_non_interactive_session=is_non_interactive_session) == "approved"}

    def enabled(name: str, config: McpServerConfig) -> bool:
        return not is_mcp_server_disabled(name, cwd) and is_mcp_server_allowed_by_policy(name, config, merged, policy)

    manual = {n: c for n, c in {**user, **approved, **local, **(dynamic_servers or {})}.items() if enabled(n, c)}
    enabled_plugin = {n: c for n, c in plugin_servers.items() if enabled(n, c)}
    deduped, _suppressed = dedup_plugin_mcp_servers(enabled_plugin, manual)
    deduped.update({n: c for n, c in plugin_servers.items() if n not in enabled_plugin})
    servers = {**deduped, **user, **approved, **local}
    filtered = {name: config for name, config in servers.items() if enabled(name, config)}
    return filtered, [*user_errors, *project_errors, *local_errors]


__all__ = [
    "CCR_PROXY_PATH_MARKERS",
    "GLOBAL_CONFIG_FILENAME",
    "MANAGED_MCP_FILENAME",
    "MCP_JSON_FILENAME",
    "SCHEMA_ERROR",
    "dedup_plugin_mcp_servers",
    "filter_mcp_servers_by_policy",
    "get_claude_code_mcp_configs",
    "get_mcp_config_by_name",
    "get_global_config_path",
    "get_mcp_configs_by_scope",
    "get_mcp_server_signature",
    "get_project_config_key",
    "get_project_mcp_server_status",
    "is_mcp_server_allowed_by_policy",
    "is_mcp_server_disabled",
    "parse_mcp_config",
    "parse_mcp_config_from_file_path",
    "set_mcp_server_enabled",
    "unwrap_ccr_proxy_url",
]
