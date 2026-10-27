"""
MCP tools as loop tools (Phase 8).

Ported from `reference/claude-code/src/services/mcp/client.ts` -> `fetchToolsForClient`'s
`{...MCPTool, ...}` wrapper and `mcpToolInputToAutoClassifierInput`;
`tools/MCPTool/MCPTool.ts` (`maxResultSizeChars: 100_000`, `checkPermissions` passthrough);
`tools/ListMcpResourcesTool/` and `tools/ReadMcpResourceTool/`.

* **Names** are `buildMcpToolName(server, tool)`, so permission rules, hook matchers and agent
  `tools` lists address `mcp__<server>__<tool>` (or the whole `mcp__<server>`).
* **Annotations**: `readOnlyHint` makes a tool read-only *and* concurrency-safe,
  `destructiveHint` destructive; absent hints are `false`.
* **Permissions**: always `passthrough` ("MCPTool requires permission."), which the gate turns
  into an ask. A TDDAgents run is headless, so an MCP call is denied unless an allow rule
  (`mcp__server__tool` or `mcp__server`) covers it. The TDD phase tier runs before any allow
  rule, as for every tool.
* **TDD barrier 2:** an MCP tool is never classified as a test or implementation writer — this
  loop cannot see what a server does with its arguments. An *allowed* MCP tool that writes
  files is therefore outside the phase-derived deny rules. Barriers 1 and 3 still hold: only
  `RunTests` moves the ledger, and the Stop hook still refuses an unproven cycle.
* **Results**: the content blocks reach the model as they are (text and images); the string
  form, which hooks and result governance read, is the text with `[image]` placeholders.
  A hook's `updatedMCPToolOutput` or a persisted preview replaces the string, and then the
  string is what the model gets.

Each wrapper is built by its own call of `build_mcp_tool`, so its closure holds its own server
and tool name: a loop that built several tools cannot leave them all calling the last one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage

from app.services.mcp.client import (
    call_mcp_tool,
    truncate_description,
)
from app.services.mcp.normalization import build_mcp_tool_name
from app.services.mcp.types import ConnectedMCPServer
from app.tool import (
    BuiltTool,
    PermissionResult,
    ToolResult,
    build_tool,
    default_map_result,
)
from app.utils.frontmatter_parser import parse_markdown_frontmatter

logger = logging.getLogger(__name__)

#: `MCPTool.ts` / the resource tools' `maxResultSizeChars`.
MCP_MAX_RESULT_SIZE_CHARS = 100_000
#: `MCPTool.ts` -> `checkPermissions`.
MCP_PERMISSION_MESSAGE = "MCPTool requires permission."


def load_prompt(path: Path) -> str:
    """A resource tool's `prompt.md` body."""
    return parse_markdown_frontmatter(path.read_text(encoding="utf-8"))[1].strip()


@dataclass(frozen=True, slots=True)
class McpBuiltTool(BuiltTool):
    """A `BuiltTool` carrying upstream's `mcpInfo` and its own `toAutoClassifierInput`."""

    server_name: str = ""
    mcp_tool_name: str = ""
    _auto_classifier_input: Callable[[Mapping[str, Any]], Any] | None = None

    def to_auto_classifier_input(self, input: Mapping[str, Any]) -> Any:
        if self._auto_classifier_input is None:
            return ""
        return self._auto_classifier_input(input)


def mcp_tool_input_to_auto_classifier_input(input: Mapping[str, Any], tool_name: str) -> str:
    """`mcpToolInputToAutoClassifierInput`."""
    if input:
        return " ".join(f"{k}={_js_string(v)}" for k, v in input.items())
    return tool_name


def _js_string(value: Any) -> str:
    """`String(value)` for JSON values."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return "[object Object]" if isinstance(value, dict) else ",".join(_js_string(v) for v in value)
    return str(value)


def _passthrough(_input: dict[str, Any], _context: Any) -> PermissionResult:
    return PermissionResult(behavior="passthrough", message=MCP_PERMISSION_MESSAGE)


def render_blocks_as_text(blocks: Sequence[Mapping[str, Any]]) -> str:
    """The string form of a block result: its text, with `[image]` for each image."""
    parts = [str(b.get("text", "")) if b.get("type") == "text" else "[image]" for b in blocks
             if b.get("type") in ("text", "image")]
    return "\n".join(parts)


def _to_langchain_block(block: Mapping[str, Any]) -> dict[str, Any]:
    if block.get("type") == "image":
        source = block.get("source") or {}
        return {"type": "image", "base64": source.get("data", ""), "mime_type": source.get("media_type", "image/png")}
    return dict(block)


def map_mcp_result(result: ToolResult, tool_use_id: str) -> ToolMessage:
    """`MCPTool.mapToolResultToToolResultBlockParam`: the blocks while the string is still theirs."""
    message = default_map_result(result, tool_use_id)
    blocks = (result.metadata or {}).get("content_blocks")
    if blocks is not None and result.content == render_blocks_as_text(blocks):
        message.content = [_to_langchain_block(b) for b in blocks]
    return message


def context_cwd(context: Any) -> str:
    root = getattr(getattr(context, "workspace", None), "root", None)
    return str(root) if root is not None else "."


def build_mcp_tool(client: ConnectedMCPServer, tool: Any) -> McpBuiltTool:
    """One server tool (`mcp.types.Tool`) as a loop tool."""
    full_name = build_mcp_tool_name(client.name, tool.name)
    annotations = tool.annotations
    read_only = bool(getattr(annotations, "read_only_hint", None) or False)
    destructive = bool(getattr(annotations, "destructive_hint", None) or False)
    description = tool.description or ""
    server_tool_name = tool.name

    async def call(input: dict[str, Any], context: Any) -> ToolResult:
        out = await call_mcp_tool(
            client, server_tool_name, input,
            run_id=str(getattr(context, "run_id", "") or "run"), cwd=context_cwd(context),
            cancel=getattr(context, "cancel", None),
        )
        content = out["content"]
        metadata: dict[str, Any] = {}
        if "_meta" in out or "structuredContent" in out:
            metadata["mcp_meta"] = {k: out[k] for k in ("_meta", "structuredContent") if k in out}
        if isinstance(content, list):
            metadata["content_blocks"] = content
            text = render_blocks_as_text(content)
        else:
            text = content or ""
        return ToolResult(content=text, metadata=metadata or None)

    built = build_tool(
        name=full_name,
        prompt=truncate_description(description),
        call=call,
        input_schema=dict(tool.input_schema or {"type": "object"}),
        description=lambda _args: description,
        is_concurrency_safe=lambda _args: read_only,
        is_read_only=lambda _args: read_only,
        is_destructive=lambda _args: destructive,
        check_permissions=_passthrough,
        map_result=map_mcp_result,
        is_mcp=True,
        max_result_size_chars=MCP_MAX_RESULT_SIZE_CHARS,
    )
    return McpBuiltTool(
        **{f: getattr(built, f) for f in BuiltTool.__dataclass_fields__},
        server_name=client.name,
        mcp_tool_name=server_tool_name,
        _auto_classifier_input=lambda args: mcp_tool_input_to_auto_classifier_input(args, server_tool_name),
    )


def mcp_clients_of(context: Any) -> tuple[Any, ...]:
    """Upstream's `options.mcpClients`, read from the context as `utils/attachments.py` does."""
    return tuple(getattr(context, "mcp_clients", ()) or ())


def available_server_names(clients: Sequence[Any]) -> str:
    return ", ".join(str(c.name) for c in clients)


__all__ = [
    "MCP_MAX_RESULT_SIZE_CHARS",
    "MCP_PERMISSION_MESSAGE",
    "McpBuiltTool",
    "build_mcp_tool",
    "map_mcp_result",
    "mcp_tool_input_to_auto_classifier_input",
    "render_blocks_as_text",
]
