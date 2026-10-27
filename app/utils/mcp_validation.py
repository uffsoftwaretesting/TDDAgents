"""
MCP output limits and storage (Phase 8).

Ported from `reference/claude-code/src/utils/mcpValidation.ts` -> `getMaxMcpOutputTokens`,
`getContentSizeEstimate`, `mcpContentNeedsTruncation`, `truncateMcpContent`,
`truncateMcpContentIfNeeded`; and `utils/mcpOutputStorage.ts` -> `getFormatDescription`,
`getLargeOutputInstructions`, `extensionForMimeType`, `persistBinaryContent`,
`getBinaryBlobSavedMessage`. Files land in the run's tool-results directory
(`utils/tool_result_storage.py`), which `ReadFile` may read.

Divergences: `mcpContentNeedsTruncation` asks the API for an exact token count once the rough
estimate passes half the limit; this port has no counting endpoint for every provider, so the
rough estimate itself is compared with the limit. Image blocks are never compressed (no image
library); a truncation that cannot fit an image drops it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from app.utils.tokens import rough_token_count_estimation
from app.utils.tool_result_storage import (
    format_file_size,
    safe_file_stem,
    tool_results_dir,
)

logger = logging.getLogger(__name__)

#: `mcpValidation.ts` constants.
MCP_TOKEN_COUNT_THRESHOLD_FACTOR = 0.5
IMAGE_TOKEN_ESTIMATE = 1600
DEFAULT_MAX_MCP_OUTPUT_TOKENS = 25000
MAX_MCP_OUTPUT_TOKENS_ENV = "TDDAGENTS_MAX_MCP_OUTPUT_TOKENS"

#: `MCPToolResult`: a string, content blocks, or nothing.
MCPToolResult = str | list[dict[str, Any]] | None
MCPResultType = Literal["toolResult", "structuredContent", "contentArray"]


def get_max_mcp_output_tokens() -> int:
    """`getMaxMcpOutputTokens` (no GrowthBook override here)."""
    raw = os.environ.get(MAX_MCP_OUTPUT_TOKENS_ENV)
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            parsed = 0
        if parsed > 0:
            return parsed
    return DEFAULT_MAX_MCP_OUTPUT_TOKENS


def get_content_size_estimate(content: MCPToolResult) -> int:
    """`getContentSizeEstimate`: rough tokens of text blocks plus a flat estimate per image."""
    if not content:
        return 0
    if isinstance(content, str):
        return rough_token_count_estimation(content)
    total = 0
    for block in content:
        if block.get("type") == "text":
            total += rough_token_count_estimation(str(block.get("text", "")))
        elif block.get("type") == "image":
            total += IMAGE_TOKEN_ESTIMATE
    return total


def mcp_content_needs_truncation(content: MCPToolResult) -> bool:
    """`mcpContentNeedsTruncation`, with the estimate standing in for the API count."""
    if not content:
        return False
    estimate = get_content_size_estimate(content)
    limit = get_max_mcp_output_tokens()
    if estimate <= limit * MCP_TOKEN_COUNT_THRESHOLD_FACTOR:
        return False
    return estimate > limit


def _truncation_message() -> str:
    return (
        f"\n\n[OUTPUT TRUNCATED - exceeded {get_max_mcp_output_tokens()} token limit]\n"
        "The tool output was truncated. If this MCP server provides pagination or filtering tools, use them to "
        "retrieve specific portions of the data. If pagination is not available, inform the user that you are "
        "working with truncated output and results may be incomplete."
    )


def _truncate_blocks(blocks: Sequence[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """`truncateContentBlocks` (an image that does not fit is dropped, not compressed)."""
    out: list[dict[str, Any]] = []
    used = 0
    for block in blocks:
        if block.get("type") == "text":
            text = str(block.get("text", ""))
            remaining = max_chars - used
            if remaining <= 0:
                break
            if len(text) <= remaining:
                out.append(block)
                used += len(text)
            else:
                out.append({"type": "text", "text": text[:remaining]})
                break
        elif block.get("type") == "image":
            image_chars = IMAGE_TOKEN_ESTIMATE * 4
            if used + image_chars <= max_chars:
                out.append(block)
                used += image_chars
        else:
            out.append(block)
    return out


def truncate_mcp_content(content: MCPToolResult) -> MCPToolResult:
    """`truncateMcpContent`: the head that fits `max tokens × 4` characters, plus the notice."""
    if not content:
        return content
    max_chars = get_max_mcp_output_tokens() * 4
    if isinstance(content, str):
        return content[:max_chars] + _truncation_message()
    return [*_truncate_blocks(content, max_chars), {"type": "text", "text": _truncation_message()}]


def truncate_mcp_content_if_needed(content: MCPToolResult) -> MCPToolResult:
    """`truncateMcpContentIfNeeded`."""
    return truncate_mcp_content(content) if mcp_content_needs_truncation(content) else content


def get_format_description(result_type: MCPResultType, schema: str | None = None) -> str:
    """`getFormatDescription`."""
    if result_type == "toolResult":
        return "Plain text"
    if result_type == "structuredContent":
        return f"JSON with schema: {schema}" if schema else "JSON"
    return f"JSON array with schema: {schema}" if schema else "JSON array"


def get_large_output_instructions(raw_output_path: str, content_length: int, format_description: str) -> str:
    """`getLargeOutputInstructions` (no `maxReadLength`: the Bash limit variant is not used here)."""
    return (
        f"Error: result ({content_length:,} characters) exceeds maximum allowed tokens. "
        f"Output has been saved to {raw_output_path}.\n"
        f"Format: {format_description}\n"
        "Use offset and limit parameters to read specific portions of the file, search within it for specific "
        "content, and jq to make structured queries.\n"
        "REQUIREMENTS FOR SUMMARIZATION/ANALYSIS/REVIEW:\n"
        f"- You MUST read the content from the file at {raw_output_path} in sequential chunks until 100% of the "
        "content has been read.\n"
        "- If you receive truncation warnings when reading the file, reduce the chunk size until you have read "
        "100% of the content without truncation.\n"
        "- Before producing ANY summary or analysis, you MUST explicitly describe what portion of the content you "
        "have read. ***If you did not read the entire content, you MUST explicitly state this.***\n"
    )


_EXTENSIONS = {
    "application/pdf": "pdf",
    "application/json": "json",
    "text/csv": "csv",
    "text/plain": "txt",
    "text/html": "html",
    "text/markdown": "md",
    "application/zip": "zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}


def extension_for_mime_type(mime_type: str | None) -> str:
    """`extensionForMimeType`."""
    if not mime_type:
        return "bin"
    return _EXTENSIONS.get(mime_type.split(";")[0].strip().lower(), "bin")


def persist_binary_content(
    data: bytes, mime_type: str | None, persist_id: str, *, run_id: str, cwd: str | Path
) -> tuple[Path, int]:
    """`persistBinaryContent`: write the bytes beside the run's tool results; raises `OSError`."""
    directory = tool_results_dir(run_id, cwd)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_file_stem(persist_id)}.{extension_for_mime_type(mime_type)}"
    path.write_bytes(data)
    return path, len(data)


def get_binary_blob_saved_message(filepath: str, mime_type: str | None, size: int, source_description: str) -> str:
    """`getBinaryBlobSavedMessage`."""
    return (
        f"{source_description}Binary content ({mime_type or 'unknown type'}, {format_file_size(size)}) "
        f"saved to {filepath}"
    )


__all__ = [
    "DEFAULT_MAX_MCP_OUTPUT_TOKENS",
    "IMAGE_TOKEN_ESTIMATE",
    "MAX_MCP_OUTPUT_TOKENS_ENV",
    "MCP_TOKEN_COUNT_THRESHOLD_FACTOR",
    "MCPResultType",
    "MCPToolResult",
    "extension_for_mime_type",
    "get_binary_blob_saved_message",
    "get_content_size_estimate",
    "get_format_description",
    "get_large_output_instructions",
    "get_max_mcp_output_tokens",
    "mcp_content_needs_truncation",
    "persist_binary_content",
    "truncate_mcp_content",
    "truncate_mcp_content_if_needed",
]
