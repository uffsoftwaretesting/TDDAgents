"""
Small MCP helpers the `/mcp` menus read.

Ported from `reference/claude-code/src/services/mcp/utils.ts` -> `filterToolsByServer`,
`describeMcpConfigFilePath`. Upstream reads the cwd and the enterprise file's location from
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


__all__ = ["describe_mcp_config_file_path", "filter_tools_by_server"]
