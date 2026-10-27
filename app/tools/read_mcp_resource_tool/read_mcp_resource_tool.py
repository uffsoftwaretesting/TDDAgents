"""
ReadMcpResourceTool: one resource from one of the run's MCP servers.

Ported from `reference/claude-code/src/tools/ReadMcpResourceTool/ReadMcpResourceTool.ts`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.mcp.client import (
    read_resource,
)
from app.services.mcp.types import ConnectedMCPServer
from app.tool import (
    BuiltTool,
    ToolResult,
    build_tool,
)
from app.tools.mcp_tool.mcp_tool import (
    MCP_MAX_RESULT_SIZE_CHARS,
    McpBuiltTool,
    available_server_names,
    context_cwd,
    load_prompt,
    mcp_clients_of,
)

READ_MCP_RESOURCE_TOOL_NAME = "ReadMcpResourceTool"


def build_read_mcp_resource_tool() -> McpBuiltTool:
    """`ReadMcpResourceTool`."""

    async def call(input: dict[str, Any], context: Any) -> ToolResult:
        clients = mcp_clients_of(context)
        server_name = str(input["server"])
        client = next((c for c in clients if c.name == server_name), None)
        if client is None:
            raise ValueError(f'Server "{server_name}" not found. Available servers: {available_server_names(clients)}')
        connected = client if isinstance(client, ConnectedMCPServer) else None
        if connected is None:
            raise ValueError(f'Server "{server_name}" is not connected')
        if not getattr(connected.capabilities, "resources", None):
            raise ValueError(f'Server "{server_name}" does not support resources')
        data = await read_resource(
            connected, str(input["uri"]), run_id=str(getattr(context, "run_id", "") or "run"), cwd=context_cwd(context)
        )
        return ToolResult(content=json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    built = build_tool(
        name=READ_MCP_RESOURCE_TOOL_NAME,
        prompt=load_prompt(Path(__file__).with_name("prompt.md")),
        call=call,
        input_schema={
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "The MCP server name"},
                "uri": {"type": "string", "description": "The resource URI to read"},
            },
            "required": ["server", "uri"],
        },
        is_concurrency_safe=lambda _args: True,
        is_read_only=lambda _args: True,
        max_result_size_chars=MCP_MAX_RESULT_SIZE_CHARS,
    )
    return McpBuiltTool(
        **{f: getattr(built, f) for f in BuiltTool.__dataclass_fields__},
        _auto_classifier_input=lambda args: f"{args.get('server')} {args.get('uri')}",
    )
