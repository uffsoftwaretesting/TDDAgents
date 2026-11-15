"""
How a file update reads under its Edit or WriteFile call.

Ported from `reference/claude-code/src/components/FileEditToolUpdatedMessage.tsx` ->
`FileEditToolUpdatedMessage`: the count of added and removed lines across the hunks ("Added 2 lines,
removed 1 line"; "Removed" when nothing was added; `line` for one), then the hunks
(`StructuredDiffList`). In upstream's `condensed` style without `verbose`, the count alone.

*Divergences:* the REPL's view is upstream's `condensed` one (user decision 2026-11-14: one line per
call, the hunks with `--verbose`), and there is no plan-file `previewHint` (`/plan to preview`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.components.structured_diff_list import structured_diff_list


def updated_summary(structured_patch: Sequence[Mapping[str, Any]]) -> str:
    """The count line: `Added N line(s)`, `, ` and `removed M line(s)`, either alone, or empty."""
    lines = [line for hunk in structured_patch for line in hunk.get("lines") or []]
    additions = sum(1 for line in lines if line.startswith("+"))
    removals = sum(1 for line in lines if line.startswith("-"))
    text = ""
    if additions > 0:
        text += f"Added {additions} {'lines' if additions > 1 else 'line'}"
    if additions > 0 and removals > 0:
        text += ", "
    if removals > 0:
        text += f"{'R' if additions == 0 else 'r'}emoved {removals} {'lines' if removals > 1 else 'line'}"
    return text


def file_edit_tool_updated_message(structured_patch: Sequence[Mapping[str, Any]], verbose: bool = False) -> str:
    """The count line; with `verbose`, then the hunks' numbered lines (`condensed` and not verbose: the count)."""
    if not verbose:
        return updated_summary(structured_patch)
    return "\n".join([updated_summary(structured_patch), *structured_diff_list(structured_patch)])


__all__ = ["file_edit_tool_updated_message", "updated_summary"]
