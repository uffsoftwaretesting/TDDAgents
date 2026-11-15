"""
How an UpdatePlan call is shown.

TDDAgents' own: UpdatePlan has no upstream counterpart. It follows upstream's renderings of a
call's input in `key: "value"` pieces joined by `, ` (`GrepTool/UI.tsx`, `GlobTool/UI.tsx`), with a
summary's text truncated to `TOOL_SUMMARY_MAX_LENGTH` columns as `getToolUseSummary` truncates a
long input: each operation is its `op`, the item `id` it names and its `text` in quotes (`add
"Parse the input"`, `edit 2 "Reject empty input"`, `remove 3`); the position of an `add` or a
`move` is left out. The whole change is the permission dialog's.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.constants.tool_limits import TOOL_SUMMARY_MAX_LENGTH
from app.utils.truncate import truncate


def _operation(operation: Mapping[str, Any]) -> str:
    parts = [str(operation.get("op"))]
    if operation.get("id"):
        parts.append(str(operation["id"]))
    if operation.get("text"):
        parts.append(f'"{truncate(str(operation["text"]), TOOL_SUMMARY_MAX_LENGTH, single_line=True)}"')
    return " ".join(parts)


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    """Each operation, `, ` between two; no row until there is one."""
    operations = [op for op in input.get("operations") or [] if isinstance(op, Mapping)]
    if not operations:
        return None
    return ", ".join(_operation(operation) for operation in operations)


__all__ = ["render_tool_use_message"]
