"""
Model Context Protocol (MCP) Router and Tool Integration (Plan B2).

Ported from:
- `reference/claude-code/src/tools/MCPTool/MCPTool.ts`
- `reference/claude-code/src/services/mcp/client.ts`
- `docs/refactoring_transition_plan.md` -> Plan B2 (MCP Router Integration)

Provides:
- `build_mcp_tool`: Wraps external MCP server capabilities into first-class `BuiltTool`
  instances with `is_mcp=True` and canonical naming prefix `mcp__<server>__<tool>`.
- `MCPServer`: Encapsulates a connected external MCP server.
- `MCPRouter`: Aggregates multiple external MCP servers into a unified tool collection
  ready for `assemble_tool_pool`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence

from app.loop.context import ToolContext
from app.loop.tools.base import BuiltTool, Tool, ToolResult, build_tool

logger = logging.getLogger(__name__)


def build_mcp_tool(
    server_name: str,
    tool_name: str,
    description: str,
    input_schema: dict[str, Any],
    handler: Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]],
    *,
    is_read_only: bool = False,
    is_concurrency_safe: bool = True,
) -> BuiltTool:
    """
    Build a standard Tool instance representing an external MCP tool.
    Canonical name format: mcp__<server_name>__<tool_name>.
    """
    canonical_name = f"mcp__{server_name}__{tool_name}"

    async def _call(input_dict: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            return await handler(input_dict, context)
        except Exception as exc:
            logger.warning("Error executing MCP tool '%s': %s", canonical_name, exc)
            return ToolResult(
                content=f"<mcp_tool_error>Error in {canonical_name}: {exc}</mcp_tool_error>",
                is_error=True,
            )

    tool = build_tool(
        name=canonical_name,
        prompt=description,
        call=_call,
        input_schema=input_schema or {"type": "object"},
        is_read_only=lambda a: is_read_only,
        is_concurrency_safe=lambda a: is_concurrency_safe,
        is_mcp=True,
    )
    return tool


@dataclass
class MCPServer:
    """Represents a single connected external MCP server."""

    name: str
    tools: dict[str, Tool] = field(default_factory=dict)

    def add_tool(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get_tools(self) -> tuple[Tool, ...]:
        return tuple(self.tools.values())


class MCPRouter:
    """
    Router managing connected MCP servers and their exposed toolsets.
    """

    def __init__(self) -> None:
        self.servers: dict[str, MCPServer] = {}

    def register_server(self, name: str, tools: Sequence[Tool] = ()) -> MCPServer:
        """Register or retrieve an MCP server."""
        if name not in self.servers:
            self.servers[name] = MCPServer(name=name)
        server = self.servers[name]
        for tool in tools:
            server.add_tool(tool)
        return server

    def register_tool(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]],
        *,
        is_read_only: bool = False,
    ) -> BuiltTool:
        """Convenience method to define and register an MCP tool."""
        server = self.register_server(server_name)
        tool = build_mcp_tool(
            server_name=server_name,
            tool_name=tool_name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            is_read_only=is_read_only,
        )
        server.add_tool(tool)
        return tool

    def get_tools(self) -> tuple[Tool, ...]:
        """Collect all tools from all registered servers."""
        all_tools: list[Tool] = []
        for server in self.servers.values():
            all_tools.extend(server.get_tools())
        return tuple(all_tools)

    def load_from_config(self, config: Mapping[str, Any]) -> None:
        """
        Load MCP server configurations (e.g. from mcpServers settings dict).
        """
        servers_cfg = config.get("mcpServers") or config
        for server_name, spec in servers_cfg.items():
            if isinstance(spec, dict):
                self.register_server(server_name)
                # If statically declared tools are present in spec
                tools_spec = spec.get("tools", [])
                for t in tools_spec:
                    if isinstance(t, dict):
                        t_name = t.get("name", "tool")
                        t_desc = t.get("description", f"{server_name} {t_name}")
                        t_schema = t.get("inputSchema", {})

                        async def dummy_handler(
                            a: dict[str, Any], c: ToolContext, _server: str = server_name, _tool: str = t_name
                        ) -> ToolResult:
                            return ToolResult(content=f"mcp call {_server}.{_tool}")

                        self.register_tool(
                            server_name=server_name,
                            tool_name=t_name,
                            description=t_desc,
                            input_schema=t_schema,
                            handler=dummy_handler,
                        )
