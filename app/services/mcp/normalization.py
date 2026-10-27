"""
MCP names (Phase 8).

Ported from `reference/claude-code/src/services/mcp/normalization.ts` -> `normalizeNameForMCP`
and `mcpStringUtils.ts` -> `mcpInfoFromString`, `getMcpPrefix`, `buildMcpToolName`,
`getMcpDisplayName`. Tool names follow the API's `^[a-zA-Z0-9_-]{1,64}$`; a model-facing MCP
tool is `mcp__<server>__<tool>`, which is also the name permission rules, hook matchers and
agent `tools` lists use.
"""

from __future__ import annotations

import re

#: `normalization.ts` -> `CLAUDEAI_SERVER_PREFIX`.
CLAUDEAI_SERVER_PREFIX = "claude.ai "

_INVALID = re.compile(r"[^a-zA-Z0-9_-]")
_UNDERSCORE_RUNS = re.compile(r"_+")


def normalize_name_for_mcp(name: str) -> str:
    """`normalizeNameForMCP`: every character outside `[a-zA-Z0-9_-]` becomes `_`."""
    normalized = _INVALID.sub("_", name)
    if name.startswith(CLAUDEAI_SERVER_PREFIX):
        normalized = _UNDERSCORE_RUNS.sub("_", normalized).strip("_")
    return normalized


def mcp_info_from_string(tool_string: str) -> tuple[str, str | None] | None:
    """`mcpInfoFromString`: `(server, tool)` of `mcp__server__tool`, the tool `None` for a server rule."""
    parts = tool_string.split("__")
    if len(parts) < 2 or parts[0] != "mcp" or not parts[1]:
        return None
    tool = "__".join(parts[2:]) if len(parts) > 2 else None
    return parts[1], tool


def get_mcp_prefix(server_name: str) -> str:
    """`getMcpPrefix`."""
    return f"mcp__{normalize_name_for_mcp(server_name)}__"


def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """`buildMcpToolName`."""
    return f"{get_mcp_prefix(server_name)}{normalize_name_for_mcp(tool_name)}"


def get_mcp_display_name(full_name: str, server_name: str) -> str:
    """`getMcpDisplayName`: the tool name without its server prefix."""
    return full_name.replace(get_mcp_prefix(server_name), "", 1)


__all__ = [
    "CLAUDEAI_SERVER_PREFIX",
    "build_mcp_tool_name",
    "get_mcp_display_name",
    "get_mcp_prefix",
    "mcp_info_from_string",
    "normalize_name_for_mcp",
]
