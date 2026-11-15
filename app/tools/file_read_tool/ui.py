"""
How a ReadFile call is named and shown.

Ported from `reference/claude-code/src/tools/FileReadTool/UI.tsx` -> `userFacingName`,
`renderToolUseMessage`, `getToolUseSummary`, `getAgentOutputTaskId`; `FileReadTool.ts` ->
`getActivityDescription`. The renderers take the `context` that stands in for upstream's
process-wide cwd (the display path, the task output directory). Not ported: the plan-file and
agent-output names (`Reading Plan`, `Read agent output`), which need the session's plan directory
and task registry.
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.file import get_display_path


def user_facing_name(input: dict[str, Any] | None) -> str:
    return "Read"


def render_tool_use_message(input: dict[str, Any], verbose: bool, context: Any = None) -> str | None:
    """
    `renderToolUseMessage`: `""` for a task's output file (its id is shown apart), else the path
    (its display path unless `verbose`) with the pages, or with the line range when `verbose`.
    """
    file_path = input.get("file_path")
    if not file_path:
        return None
    if get_agent_output_task_id(str(file_path), context):
        return ""
    display = str(file_path) if verbose else get_display_path(str(file_path), context)
    pages = input.get("pages")
    if pages:
        return f"{display} · pages {pages}"
    offset, limit = input.get("offset"), input.get("limit")
    if verbose and (offset or limit):
        start = offset or 1
        line_range = f"lines {start}-{start + limit - 1}" if limit else f"from line {start}"
        return f"{display} · {line_range}"
    return display


#: `getAgentOutputTaskId`'s task-id shape.
_TASK_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def get_agent_output_task_id(file_path: str, context: Any = None) -> str | None:
    """`getAgentOutputTaskId`: the task id when `file_path` is a task's `.output` file, else `None`."""
    from app.utils.task.disk_output import get_task_output_dir

    if context is None:  # no run, so no task directory to be under
        return None
    prefix = f"{get_task_output_dir(context)}/"
    suffix = ".output"
    if file_path.startswith(prefix) and file_path.endswith(suffix):
        task_id = file_path[len(prefix):-len(suffix)]
        if 0 < len(task_id) <= 20 and _TASK_ID.match(task_id):
            return task_id
    return None


def get_tool_use_summary(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`getToolUseSummary`: a task output file's task id, else the file's display path."""
    if not input or not input.get("file_path"):
        return None
    file_path = str(input["file_path"])
    return get_agent_output_task_id(file_path, context) or get_display_path(file_path, context)


def get_activity_description(input: dict[str, Any] | None, context: Any = None) -> str | None:
    """`FileReadTool.ts` -> `getActivityDescription`."""
    summary = get_tool_use_summary(input, context)
    return f"Reading {summary}" if summary else "Reading file"


def render_tool_result_message(output: Any, verbose: bool) -> str | None:
    """`renderToolResultMessage`: what was read, by output type; `None` for an unknown output."""
    from app.utils.tool_result_storage import format_file_size

    if not isinstance(output, dict):
        return None
    file = output.get("file") or {}
    kind = output.get("type")
    if kind == "image":
        return f"Read image ({format_file_size(int(file.get('originalSize', 0)))})"
    if kind == "notebook":
        cells = file.get("cells") or []
        return f"Read {len(cells)} cells" if cells else "No cells found in notebook"
    if kind == "pdf":
        return f"Read PDF ({format_file_size(int(file.get('originalSize', 0)))})"
    if kind == "parts":
        count = int(file.get("count", 0))
        return (f"Read {count} {'page' if count == 1 else 'pages'} "
                f"({format_file_size(int(file.get('originalSize', 0)))})")
    if kind == "text":
        lines = int(file.get("numLines", 0))
        return f"Read {lines} {'line' if lines == 1 else 'lines'}"
    if kind == "file_unchanged":
        return "Unchanged since last read"
    return None
