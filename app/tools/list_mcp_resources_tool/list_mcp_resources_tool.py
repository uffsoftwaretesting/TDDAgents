"""
ListMcpResourcesTool: the resources the run's MCP servers offer.

Ported from `reference/claude-code/src/tools/ListMcpResourcesTool/ListMcpResourcesTool.ts`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.mcp.client import (
    fetch_resources_for_client,
)
from app.tool import (
    BuiltTool,
    ToolResult,
    build_tool,
)
from app.tools.mcp_tool.mcp_tool import (
    MCP_MAX_RESULT_SIZE_CHARS,
    McpBuiltTool,
    available_server_names,
    load_prompt,
    mcp_clients_of,
)

LIST_MCP_RESOURCES_TOOL_NAME = "ListMcpResourcesTool"

#: `ListMcpResourcesTool.mapToolResultToToolResultBlockParam`'s empty answer.
NO_RESOURCES_MESSAGE = "No resources found. MCP servers may still provide tools even if they have no resources."


def build_list_mcp_resources_tool() -> McpBuiltTool:
    """`ListMcpResourcesTool`."""

    async def call(input: dict[str, Any], context: Any) -> ToolResult:
        clients = mcp_clients_of(context)
        target = input.get("server")
        chosen = [c for c in clients if c.name == target] if target else list(clients)
        if target and not chosen:
            raise ValueError(f'Server "{target}" not found. Available servers: {available_server_names(clients)}')
        resources = [r.as_dict() for c in chosen for r in await fetch_resources_for_client(c)]
        if not resources:
            return ToolResult(content=NO_RESOURCES_MESSAGE)
        return ToolResult(content=json.dumps(resources, ensure_ascii=False, separators=(",", ":")))

    built = build_tool(
        name=LIST_MCP_RESOURCES_TOOL_NAME,
        prompt=load_prompt(Path(__file__).with_name("prompt.md")),
        call=call,
        input_schema={
            "type": "object",
            "properties": {"server": {"type": "string", "description": "Optional server name to filter resources by"}},
        },
        is_concurrency_safe=lambda _args: True,
        is_read_only=lambda _args: True,
        max_result_size_chars=MCP_MAX_RESULT_SIZE_CHARS,
    )
    return McpBuiltTool(
        **{f: getattr(built, f) for f in BuiltTool.__dataclass_fields__},
        _auto_classifier_input=lambda args: args.get("server") or "",
    )
