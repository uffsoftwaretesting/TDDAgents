"""
The warning both project-server approval dialogs open with.

Ported from `reference/claude-code/src/components/MCPServerDialogCopy.tsx` -> `MCPServerDialogCopy`.
Upstream's closing "Learn more in the MCP documentation." links to Claude Code's documentation,
which TDDAgents has no counterpart for, so the sentence is dropped.
"""

from __future__ import annotations

MCP_SERVER_DIALOG_COPY = (
    "MCP servers may execute code or access system resources. All tool calls require approval.")


__all__ = ["MCP_SERVER_DIALOG_COPY"]
