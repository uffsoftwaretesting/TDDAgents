"""
Where a run's MCP servers come from (Phase 8).

Ported from `reference/claude-code/src/services/mcp/config.ts` -> `parseMcpConfig`,
`parseMcpConfigFromFilePath`, `expandEnvVars`, `getMcpConfigsByScope`,
`getClaudeCodeMcpConfigs`, `getEnterpriseMcpFilePath`, `doesEnterpriseMcpConfigExist`,
`isMcpServerDenied` / `isMcpServerAllowedByPolicy`, `isMcpServerDisabled`; and
`services/mcp/utils.ts` -> `getProjectMcpServerStatus`.

Scopes, lowest to highest precedence: user (`mcpServers` in the global config file), project
(every `.mcp.json` from the filesystem root down to the cwd, nearer files winning), local (the
global config's `projects[<project key>].mcpServers`). An enterprise `managed-mcp.json` is
exclusive: when it exists, it is the only source. The global config file mirrors upstream's
`~/.claude.json` as `~/.tddagents.json` (`$TDDAGENTS_CONFIG_DIR/.tddagents.json` when set,
as `CLAUDE_CONFIG_DIR` does for `getGlobalClaudeFile`). The project key is the cwd's git root,
else the cwd (`getProjectPathForConfig`).

Project servers need approval, and a TDDAgents run is always non-interactive, so — exactly as
`claude -p` — they are approved unless `disabledMcpjsonServers` names them. Not ported: plugin
servers and their dedup, claude.ai connectors, dynamic `--mcp-config` servers, the Windows
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

from app.loop.hooks.config import is_restricted_to_plugin_only
from app.loop.mcp.env_expansion import expand_env_vars_in_string
from app.loop.mcp.normalization import normalize_name_for_mcp
from app.loop.mcp.types import (
    McpServerConfig,
    ValidationError,
    adheres_to_server_schema,
    server_config_from_raw,
)
from app.loop.permissions.settings import (
    SettingsLocations,
    get_settings_for_source,
    load_settings,
)
from app.loop.permissions.types import PermissionRuleSource

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
        for error in real:
            logger.error("MCP config errors for %s: %s", path, error.message)
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


def get_project_mcp_server_status(name: str, settings: Mapping[str, Any]) -> Literal["approved", "rejected"]:
    """`getProjectMcpServerStatus` for a non-interactive session: never `pending`."""
    normalized = normalize_name_for_mcp(name)
    disabled = settings.get("disabledMcpjsonServers") or []
    if any(normalize_name_for_mcp(str(n)) == normalized for n in disabled):
        return "rejected"
    return "approved"


def is_mcp_server_disabled(name: str, cwd: str | Path) -> bool:
    """`isMcpServerDisabled`: the project's `disabledMcpServers` in the global config."""
    return name in (_project_config(cwd).get("disabledMcpServers") or [])


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


def get_claude_code_mcp_configs(
    cwd: str | Path, locations: SettingsLocations
) -> tuple[dict[str, McpServerConfig], list[ValidationError]]:
    """`getClaudeCodeMcpConfigs`: the run's servers, merged, approved and filtered by policy."""
    merged = load_settings(locations)
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations) or {}
    enterprise, enterprise_errors = get_mcp_configs_by_scope("enterprise", cwd, locations)
    if (Path(locations.managed_dir) / MANAGED_MCP_FILENAME).is_file() and not enterprise_errors:
        return {n: c for n, c in enterprise.items() if is_mcp_server_allowed_by_policy(n, c, merged, policy)}, []
    if is_restricted_to_plugin_only(policy, "mcp"):
        return {}, []
    user, user_errors = get_mcp_configs_by_scope("user", cwd, locations)
    project, project_errors = get_mcp_configs_by_scope("project", cwd, locations)
    local, local_errors = get_mcp_configs_by_scope("local", cwd, locations)
    approved = {n: c for n, c in project.items() if get_project_mcp_server_status(n, merged) == "approved"}
    servers = {**user, **approved, **local}
    filtered = {
        name: config for name, config in servers.items()
        if not is_mcp_server_disabled(name, cwd) and is_mcp_server_allowed_by_policy(name, config, merged, policy)
    }
    return filtered, [*user_errors, *project_errors, *local_errors]


__all__ = [
    "GLOBAL_CONFIG_FILENAME",
    "MANAGED_MCP_FILENAME",
    "MCP_JSON_FILENAME",
    "SCHEMA_ERROR",
    "get_claude_code_mcp_configs",
    "get_global_config_path",
    "get_mcp_configs_by_scope",
    "get_project_config_key",
    "get_project_mcp_server_status",
    "is_mcp_server_allowed_by_policy",
    "is_mcp_server_disabled",
    "parse_mcp_config",
    "parse_mcp_config_from_file_path",
]
