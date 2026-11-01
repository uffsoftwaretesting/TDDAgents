"""
How an MCP call's result is shown.

Ported from `reference/claude-code/src/tools/MCPTool/UI.tsx` -> `renderToolResultMessage` (the
plain-output path: `MCP_RICH_OUTPUT` is a compile-time flag, off in the external build) and
`MCP_OUTPUT_WARNING_THRESHOLD_TOKENS`. The compact Slack-send line is not ported (it renders only
outside verbose mode, which never shows a collapsed call's result here).
"""

from __future__ import annotations

from typing import Any

from app.utils.mcp_validation import get_content_size_estimate

#: `MCP_OUTPUT_WARNING_THRESHOLD_TOKENS`.
MCP_OUTPUT_WARNING_THRESHOLD_TOKENS = 10_000


def render_tool_result_message(output: Any, verbose: bool) -> str | None:
    """The result's text blocks (`[Image]` for an image), `(No content)`, and the size warning."""
    if isinstance(output, list):
        content = "\n".join("[Image]" if isinstance(b, dict) and b.get("type") == "image"
                            else str(b.get("text") or "") if isinstance(b, dict) and b.get("type") == "text" else ""
                            for b in output)
    elif not output:
        content = "(No content)"
    else:
        content = str(output)
    tokens = get_content_size_estimate(output) if output else 0
    if tokens > MCP_OUTPUT_WARNING_THRESHOLD_TOKENS:
        return f"⚠ Large MCP response (~{tokens:,} tokens), this can fill up context quickly\n{content}"
    return content
