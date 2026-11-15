"""
How a failed tool call reads when its tool has no error rendering of its own.

Ported from `reference/claude-code/src/components/FallbackToolUseErrorMessage.tsx` ->
`FallbackToolUseErrorMessage`: the error inside `<tool_use_error>` (the whole result when there is no
such tag), without `<sandbox_violations>` blocks and `<error>` tags (they are for the model, not the
UI); an input validation error reads "Invalid tool parameters" unless verbose; any other text not
starting with `Error: ` or `Cancelled: ` gets `Error: ` in front. Unless verbose, at most
`MAX_RENDERED_LINES` lines show, then `… +N lines`.

*Divergence:* upstream's hint names the transcript shortcut (`(ctrl+o to see all)`); the REPL has no
transcript view, so the hint is the count alone (`--verbose` shows every line).
"""

from __future__ import annotations

from typing import Any

from app.utils.messages import extract_tag
from app.utils.sandbox.sandbox_ui_utils import remove_sandbox_violation_tags

#: `MAX_RENDERED_LINES`.
MAX_RENDERED_LINES = 10


def fallback_tool_use_error_text(result: Any, verbose: bool) -> str:
    """The error text: every line when `verbose`, else the first `MAX_RENDERED_LINES` and the count left."""
    if not isinstance(result, str):
        error = "Tool execution failed"
    else:
        extracted = extract_tag(result, "tool_use_error") or result
        trimmed = remove_sandbox_violation_tags(extracted).replace("<error>", "").replace("</error>", "").strip()
        if not verbose and "InputValidationError: " in trimmed:
            error = "Invalid tool parameters"
        elif trimmed.startswith(("Error: ", "Cancelled: ")):
            error = trimmed
        else:
            error = f"Error: {trimmed}"
    if verbose:
        return error
    lines = error.split("\n")
    plus_lines = len(lines) - MAX_RENDERED_LINES
    shown = "\n".join(lines[:MAX_RENDERED_LINES])
    return f"{shown}\n… +{plus_lines} {'line' if plus_lines == 1 else 'lines'}" if plus_lines > 0 else shown


__all__ = ["MAX_RENDERED_LINES", "fallback_tool_use_error_text"]
