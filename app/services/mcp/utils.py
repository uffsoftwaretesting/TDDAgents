"""
Small MCP helpers the `/mcp` menus read.

Ported from `reference/claude-code/src/services/mcp/utils.ts` -> `filterToolsByServer`,
`describeMcpConfigFilePath`, `getScopeLabel`. Upstream reads the cwd and the enterprise file's location from
module state; here the caller passes the project folder and the policy directory.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.services.mcp.config import MANAGED_MCP_FILENAME, MCP_JSON_FILENAME, get_global_config_path
from app.services.mcp.normalization import get_mcp_prefix


def filter_tools_by_server(tools: Sequence[Any], server_name: str) -> list[Any]:
    """`filterToolsByServer`: the tools named `mcp__<server>__…`."""
    prefix = get_mcp_prefix(server_name)
    return [tool for tool in tools if str(getattr(tool, "name", "") or "").startswith(prefix)]


def describe_mcp_config_file_path(scope: str, cwd: str | Path, managed_dir: str | Path | None = None) -> str:
    """`describeMcpConfigFilePath`: where a scope's servers are configured."""
    if scope == "user":
        return str(get_global_config_path())
    if scope == "project":
        return str(Path(cwd) / MCP_JSON_FILENAME)
    if scope == "local":
        return f"{get_global_config_path()} [project: {cwd}]"
    if scope == "dynamic":
        return "Dynamically configured"
    if scope == "enterprise":
        return str(Path(managed_dir) / MANAGED_MCP_FILENAME) if managed_dir is not None else MANAGED_MCP_FILENAME
    if scope == "claudeai":
        return "claude.ai"
    return scope


#: `getScopeLabel`'s labels.
SCOPE_LABELS: dict[str, str] = {
    "local": "Local config (private to you in this project)",
    "project": "Project config (shared via .mcp.json)",
    "user": "User config (available in all your projects)",
    "dynamic": "Dynamic config (from command line)",
    "enterprise": "Enterprise config (managed by your organization)",
    "claudeai": "claude.ai config",
}


def get_scope_label(scope: str) -> str:
    """`getScopeLabel`: a configuration scope as `/doctor` names it."""
    return SCOPE_LABELS.get(scope, scope)


__all__ = ["SCOPE_LABELS", "describe_mcp_config_file_path", "filter_tools_by_server", "get_scope_label"]
