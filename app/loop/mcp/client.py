"""
Connecting to an MCP server, listing what it offers, and calling its tools (Phase 8).

Ported from `reference/claude-code/src/services/mcp/client.ts`:

* `connectToServer` -> `connect_to_server`: the transport, a `Client` advertising `roots`
  and `elicitation`, the `MCP_TIMEOUT` race (`TDDAGENTS_MCP_TIMEOUT`, 30 000 ms), the
  instructions cap (`MAX_MCP_DESCRIPTION_LENGTH`), and a failure returned as a
  `FailedMCPServer`. `roots/list` answers the original cwd. Elicitation is answered `cancel`,
  as upstream's handler does in a non-interactive session.
* `fetchToolsForClient` / `fetchResourcesForClient` -> `fetch_tools_for_client` /
  `fetch_resources_for_client`: empty when the server lacks the capability or the request
  fails.
* `callMCPTool` -> `call_mcp_tool`: the `MCP_TOOL_TIMEOUT` race (`TDDAGENTS_MCP_TOOL_TIMEOUT`,
  100 000 000 ms), `isError` raised as `McpToolCallError` with the first text block, and
  `processMCPResult`.
* `transformResultContent`, `inferCompactSchema`, `transformMCPResult`, `processMCPResult`.

The SDK's session lives in anyio task scopes, which must be entered and left by one task.
Each connection therefore owns a holder task that opens the transport and the session, waits
for `cleanup()`, and closes them in order — the shape of upstream's `client.close()`.

Image blocks use the Anthropic shape (`{"type": "image", "source": {...}}`) internally, as
upstream's `ContentBlockParam`; the tool's result mapper turns them into LangChain blocks.

Not ported: OAuth and `needs-auth`, `McpSessionExpiredError`'s reconnect-and-retry and
`ensureConnectedClient`'s memoized reconnect (a failed session stays failed for the run), URL
elicitation, progress events, image resizing (`maybeResizeAndDownsampleImageBuffer`), the IDE
special cases and analytics.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Mapping, Sequence
from pathlib import Path
from typing import Any

from mcp.client.session import ClientRequestContext, ClientSession, ListRootsFnT
from mcp.types import (
    ElicitRequestParams,
    ElicitResult,
    Implementation,
    ListRootsResult,
    Root,
)
from pydantic import FileUrl

from app.loop.mcp.normalization import normalize_name_for_mcp
from app.loop.mcp.output import (
    MCPResultType,
    MCPToolResult,
    get_binary_blob_saved_message,
    get_content_size_estimate,
    get_format_description,
    get_large_output_instructions,
    mcp_content_needs_truncation,
    persist_binary_content,
    truncate_mcp_content_if_needed,
)
from app.loop.mcp.transports import TransportFactory, client_version, open_transport
from app.loop.mcp.types import (
    ConnectedMCPServer,
    FailedMCPServer,
    McpServerConfig,
    MCPServerConnection,
    ServerResource,
)
from app.loop.tools.result_storage import tool_results_dir

logger = logging.getLogger(__name__)

#: `client.ts` constants.
DEFAULT_CONNECTION_TIMEOUT_MS = 30000
DEFAULT_MCP_TOOL_TIMEOUT_MS = 100_000_000
MAX_MCP_DESCRIPTION_LENGTH = 2048
DESCRIPTION_TRUNCATION_SUFFIX = "… [truncated]"
MCP_TIMEOUT_ENV = "TDDAGENTS_MCP_TIMEOUT"
MCP_TOOL_TIMEOUT_ENV = "TDDAGENTS_MCP_TOOL_TIMEOUT"
#: `processMCPResult`: `ENABLE_MCP_LARGE_OUTPUT_FILES` defined-falsy truncates instead of saving.
LARGE_OUTPUT_FILES_ENV = "TDDAGENTS_ENABLE_MCP_LARGE_OUTPUT_FILES"
IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
#: How often an in-flight call checks the context's `CancelToken`, as the hook runner does.
CANCEL_POLL_SECONDS = 0.05

_LEADING_INT = re.compile(r"^\s*([+-]?\d+)")


def parse_int_env(name: str, default: int) -> int:
    """`parseInt(process.env.X || '', 10) || default`: a leading integer, `0`/NaN fall back."""
    match = _LEADING_INT.match(os.environ.get(name, ""))
    value = int(match.group(1)) if match else 0
    return value or default


def get_connection_timeout_ms() -> int:
    """`getConnectionTimeoutMs`."""
    return parse_int_env(MCP_TIMEOUT_ENV, DEFAULT_CONNECTION_TIMEOUT_MS)


def get_mcp_tool_timeout_ms() -> int:
    """`getMcpToolTimeoutMs`."""
    return parse_int_env(MCP_TOOL_TIMEOUT_ENV, DEFAULT_MCP_TOOL_TIMEOUT_MS)


def truncate_description(text: str) -> str:
    """The `MAX_MCP_DESCRIPTION_LENGTH` cap on tool descriptions and server instructions."""
    if len(text) > MAX_MCP_DESCRIPTION_LENGTH:
        return text[:MAX_MCP_DESCRIPTION_LENGTH] + DESCRIPTION_TRUNCATION_SUFFIX
    return text


class McpToolCallError(Exception):
    """`McpToolCallError`: the server answered `isError: true`; `meta` is the result's `_meta`."""

    def __init__(self, message: str, meta: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.meta = meta


async def _cancel_elicitation(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
    return ElicitResult(action="cancel")


def _roots_callback(cwd: str | Path) -> ListRootsFnT:
    async def list_roots(context: ClientRequestContext) -> ListRootsResult:
        return ListRootsResult(roots=[Root(uri=FileUrl(f"file://{Path(cwd).resolve()}"))])

    return list_roots


async def connect_to_server(
    name: str,
    config: McpServerConfig,
    *,
    cwd: str | Path,
    transport_factory: TransportFactory = open_transport,
) -> MCPServerConnection:
    """`connectToServer`: a `ConnectedMCPServer` whose `cleanup` closes it, or a `FailedMCPServer`."""
    ready = asyncio.Event()
    stop = asyncio.Event()
    box: dict[str, Any] = {}

    async def hold() -> None:
        try:
            async with (
                transport_factory(name, config) as streams,
                ClientSession(
                    streams[0],
                    streams[1],
                    elicitation_callback=_cancel_elicitation,
                    list_roots_callback=_roots_callback(cwd),
                    client_info=Implementation(name="tddagents", title="TDDAgents", version=client_version()),
                ) as session,
            ):
                box["init"] = await session.initialize()
                box["session"] = session
                ready.set()
                await stop.wait()
        except Exception as exc:  # noqa: BLE001 - upstream's catch: a failed connection is a state
            box["error"] = exc
        finally:
            ready.set()

    task = asyncio.create_task(hold(), name=f"mcp:{name}")
    timeout_ms = get_connection_timeout_ms()
    try:
        await asyncio.wait_for(ready.wait(), timeout_ms / 1000)
    except TimeoutError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        message = f'MCP server "{name}" connection timed out after {timeout_ms}ms'
        logger.warning(message)
        return FailedMCPServer(name=name, config=config, error=message)

    if "session" not in box:
        await asyncio.gather(task, return_exceptions=True)
        error = box.get("error")
        logger.warning("MCP server %s failed to connect: %s", name, error)
        return FailedMCPServer(name=name, config=config, error=str(error) if error is not None else None)

    async def cleanup() -> None:
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    init = box["init"]
    raw_instructions = init.instructions
    return ConnectedMCPServer(
        name=name,
        config=config,
        session=box["session"],
        capabilities=init.capabilities,
        instructions=truncate_description(raw_instructions) if raw_instructions else raw_instructions,
        server_info=init.server_info,
        cleanup=cleanup,
    )


async def fetch_tools_for_client(client: MCPServerConnection) -> list[Any]:
    """`fetchToolsForClient`'s request half: the server's `Tool` definitions (wrapped in `tools.py`)."""
    if not isinstance(client, ConnectedMCPServer) or not getattr(client.capabilities, "tools", None):
        return []
    try:
        result = await client.session.list_tools()
    except Exception as exc:  # noqa: BLE001 - upstream logs and returns []
        logger.warning("MCP server %s: failed to fetch tools: %s", client.name, exc)
        return []
    return list(result.tools)


async def fetch_resources_for_client(client: MCPServerConnection) -> list[ServerResource]:
    """`fetchResourcesForClient`."""
    if not isinstance(client, ConnectedMCPServer) or not getattr(client.capabilities, "resources", None):
        return []
    try:
        result = await client.session.list_resources()
    except Exception as exc:  # noqa: BLE001 - upstream logs and returns []
        logger.warning("MCP server %s: failed to fetch resources: %s", client.name, exc)
        return []
    return [
        ServerResource(
            uri=str(r.uri), name=r.name, server=client.name, mime_type=r.mime_type, description=r.description
        )
        for r in result.resources or ()
    ]


def _persist_id(server_name: str, kind: str) -> str:
    return f"mcp-{normalize_name_for_mcp(server_name)}-{kind}-{int(time.time() * 1000)}-{secrets.token_hex(3)}"


def _persist_blob_to_text_block(
    data: bytes, mime_type: str | None, server_name: str, source_description: str, *, run_id: str, cwd: str | Path
) -> list[dict[str, Any]]:
    """`persistBlobToTextBlock`."""
    try:
        path, size = persist_binary_content(data, mime_type, _persist_id(server_name, "blob"), run_id=run_id, cwd=cwd)
    except OSError as exc:
        return [{
            "type": "text",
            "text": f"{source_description}Binary content ({mime_type or 'unknown type'}, {len(data)} bytes) "
                    f"could not be saved to disk: {exc}",
        }]
    return [{"type": "text", "text": get_binary_blob_saved_message(str(path), mime_type, size, source_description)}]


def _image_block(data: str, mime_type: str | None) -> dict[str, Any]:
    ext = (mime_type or "").split("/")[1] if mime_type and "/" in mime_type else "png"
    return {"type": "image", "source": {"type": "base64", "media_type": f"image/{ext}", "data": data}}


def transform_result_content(
    item: Mapping[str, Any], server_name: str, *, run_id: str, cwd: str | Path
) -> list[dict[str, Any]]:
    """`transformResultContent`: one MCP content item as API content blocks."""
    kind = item.get("type")
    if kind == "text":
        return [{"type": "text", "text": item.get("text", "")}]
    if kind == "audio":
        return _persist_blob_to_text_block(
            base64.b64decode(str(item.get("data", ""))), item.get("mimeType"), server_name,
            f"[Audio from {server_name}] ", run_id=run_id, cwd=cwd,
        )
    if kind == "image":
        return [_image_block(str(item.get("data", "")), item.get("mimeType"))]
    if kind == "resource":
        resource = item.get("resource") or {}
        prefix = f"[Resource from {server_name} at {resource.get('uri')}] "
        if "text" in resource:
            return [{"type": "text", "text": f"{prefix}{resource['text']}"}]
        if "blob" in resource:
            mime_type = resource.get("mimeType")
            if (mime_type or "") in IMAGE_MIME_TYPES:
                return [{"type": "text", "text": prefix}, _image_block(str(resource["blob"]), mime_type)]
            return _persist_blob_to_text_block(
                base64.b64decode(str(resource["blob"])), mime_type, server_name, prefix, run_id=run_id, cwd=cwd
            )
        return []
    if kind == "resource_link":
        text = f"[Resource link: {item.get('name')}] {item.get('uri')}"
        if item.get("description"):
            text += f" ({item['description']})"
        return [{"type": "text", "text": text}]
    return []


def _js_typeof(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"


def infer_compact_schema(value: Any, depth: int = 2) -> str:
    """`inferCompactSchema`: a jq-friendly type signature."""
    if value is None:
        return "null"
    if isinstance(value, list):
        if not value:
            return "[]"
        return f"[{infer_compact_schema(value[0], depth - 1)}]"
    if isinstance(value, dict):
        if depth <= 0:
            return "{...}"
        entries = list(value.items())[:10]
        props = ", ".join(f"{k}: {infer_compact_schema(v, depth - 1)}" for k, v in entries)
        suffix = ", ..." if len(value) > 10 else ""
        return f"{{{props}{suffix}}}"
    return _js_typeof(value)


def _json_stringify(value: Any, indent: int | None = None) -> str:
    separators = (",", ":") if indent is None else (",", ": ")
    return json.dumps(value, ensure_ascii=False, indent=indent, separators=separators)


def transform_mcp_result(
    result: Mapping[str, Any], tool: str, name: str, *, run_id: str, cwd: str | Path
) -> tuple[MCPToolResult, MCPResultType, str | None]:
    """`transformMCPResult`: `(content, type, schema)`; an unknown shape raises."""
    if "toolResult" in result:
        return str(result["toolResult"]), "toolResult", None
    if result.get("structuredContent") is not None:
        structured = result["structuredContent"]
        return _json_stringify(structured), "structuredContent", infer_compact_schema(structured)
    if isinstance(result.get("content"), list):
        blocks = [
            block for item in result["content"]
            for block in transform_result_content(item, name, run_id=run_id, cwd=cwd)
        ]
        return blocks, "contentArray", infer_compact_schema(blocks)
    raise ValueError(f'MCP server "{name}" tool "{tool}": unexpected response format')


def _env_defined_falsy(name: str) -> bool:
    """`isEnvDefinedFalsy`."""
    value = os.environ.get(name)
    return value is not None and value.strip().lower() in ("0", "false", "no", "off")


def process_mcp_result(
    result: Mapping[str, Any], tool: str, name: str, *, run_id: str, cwd: str | Path
) -> MCPToolResult:
    """`processMCPResult`: small results pass; large ones are saved to a file, or truncated."""
    content, result_type, schema = transform_mcp_result(result, tool, name, run_id=run_id, cwd=cwd)
    if not mcp_content_needs_truncation(content):
        return content
    if _env_defined_falsy(LARGE_OUTPUT_FILES_ENV):
        return truncate_mcp_content_if_needed(content)
    if not content:
        return content
    if not isinstance(content, str) and any(block.get("type") == "image" for block in content):
        return truncate_mcp_content_if_needed(content)
    content_str = content if isinstance(content, str) else _json_stringify(content, indent=2)
    persist_id = (
        f"mcp-{normalize_name_for_mcp(name)}-{normalize_name_for_mcp(tool)}-{int(time.time() * 1000)}"
    )
    try:
        directory = tool_results_dir(run_id, cwd)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{persist_id}.{'txt' if isinstance(content, str) else 'json'}"
        path.write_text(content_str, encoding="utf-8")
    except OSError as exc:
        return (
            f"Error: result ({len(content_str):,} characters) exceeds maximum allowed tokens. Failed to save output "
            f"to file: {exc}. If this MCP server provides pagination or filtering tools, use them to retrieve "
            "specific portions of the data."
        )
    logger.debug("MCP %s/%s output persisted (%d estimated tokens)", name, tool, get_content_size_estimate(content))
    return get_large_output_instructions(str(path), len(content_str), get_format_description(result_type, schema))


async def _race_cancel(awaitable: Awaitable[Any], cancel: Any) -> Any:
    """Await `awaitable`, abandoning it when `cancel.cancelled` turns true (upstream's `signal`)."""
    call = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({call}, timeout=CANCEL_POLL_SECONDS)
            if done:
                return call.result()
            if cancel is not None and getattr(cancel, "cancelled", False):
                call.cancel()
                raise asyncio.CancelledError("MCP tool call aborted")
    finally:
        if not call.done():
            call.cancel()


async def call_mcp_tool(
    client: ConnectedMCPServer,
    tool: str,
    args: Mapping[str, Any],
    *,
    run_id: str,
    cwd: str | Path,
    meta: Mapping[str, Any] | None = None,
    cancel: Any = None,
) -> dict[str, Any]:
    """`callMCPTool`: `{"content", "_meta"?, "structuredContent"?}`; `isError` raises `McpToolCallError`."""
    timeout_ms = get_mcp_tool_timeout_ms()
    try:
        raw = await asyncio.wait_for(
            _race_cancel(
                client.session.call_tool(tool, dict(args), read_timeout_seconds=timeout_ms / 1000,
                                         meta=dict(meta) if meta else None),
                cancel,
            ),
            timeout_ms / 1000,
        )
    except TimeoutError:
        raise TimeoutError(f'MCP server "{client.name}" tool "{tool}" timed out after {timeout_ms // 1000}s') from None
    result = raw.model_dump(by_alias=True, exclude_none=True)
    if result.get("isError"):
        details = "Unknown error"
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and "text" in first:
                details = str(first["text"])
        elif "error" in result:
            details = str(result["error"])
        logger.warning("MCP server %s: %s", client.name, details)
        raise McpToolCallError(details, result.get("_meta"))
    out: dict[str, Any] = {
        "content": process_mcp_result(result, tool, client.name, run_id=run_id, cwd=cwd),
    }
    if result.get("_meta") is not None:
        out["_meta"] = result["_meta"]
    if result.get("structuredContent") is not None:
        out["structuredContent"] = result["structuredContent"]
    return out


async def read_resource(
    client: ConnectedMCPServer, uri: str, *, run_id: str, cwd: str | Path
) -> dict[str, Any]:
    """`ReadMcpResourceTool.call`'s request and content mapping: `{"contents": [...]}`."""
    result = await client.session.read_resource(uri)
    contents: list[dict[str, Any]] = []
    for index, item in enumerate(result.model_dump(by_alias=True, exclude_none=True).get("contents", [])):
        entry: dict[str, Any] = {"uri": item.get("uri")}
        if item.get("mimeType") is not None:
            entry["mimeType"] = item["mimeType"]
        if "text" in item:
            entry["text"] = item["text"]
        elif isinstance(item.get("blob"), str):
            persist_id = f"mcp-resource-{int(time.time() * 1000)}-{index}-{secrets.token_hex(3)}"
            try:
                path, size = persist_binary_content(
                    base64.b64decode(item["blob"]), item.get("mimeType"), persist_id, run_id=run_id, cwd=cwd
                )
            except OSError as exc:
                entry["text"] = f"Binary content could not be saved to disk: {exc}"
            else:
                entry["blobSavedTo"] = str(path)
                entry["text"] = get_binary_blob_saved_message(
                    str(path), item.get("mimeType"), size, f"[Resource from {client.name} at {item.get('uri')}] "
                )
        contents.append(entry)
    return {"contents": contents}


def get_mcp_instructions(clients: Sequence[MCPServerConnection]) -> str | None:
    """`constants/prompts.ts` -> `getMcpInstructions`: the system-prompt section, or `None`."""
    blocks = [
        f"## {c.name}\n{c.instructions}"
        for c in clients
        if isinstance(c, ConnectedMCPServer) and c.instructions
    ]
    if not blocks:
        return None
    return (
        "# MCP Server Instructions\n\n"
        "The following MCP servers have provided instructions for how to use their tools and resources:\n\n"
        + "\n\n".join(blocks)
    )


__all__ = [
    "CANCEL_POLL_SECONDS",
    "DEFAULT_CONNECTION_TIMEOUT_MS",
    "DEFAULT_MCP_TOOL_TIMEOUT_MS",
    "DESCRIPTION_TRUNCATION_SUFFIX",
    "IMAGE_MIME_TYPES",
    "LARGE_OUTPUT_FILES_ENV",
    "MAX_MCP_DESCRIPTION_LENGTH",
    "MCP_TIMEOUT_ENV",
    "MCP_TOOL_TIMEOUT_ENV",
    "McpToolCallError",
    "call_mcp_tool",
    "connect_to_server",
    "fetch_resources_for_client",
    "fetch_tools_for_client",
    "get_connection_timeout_ms",
    "get_mcp_instructions",
    "get_mcp_tool_timeout_ms",
    "infer_compact_schema",
    "parse_int_env",
    "process_mcp_result",
    "read_resource",
    "transform_mcp_result",
    "transform_result_content",
    "truncate_description",
]
