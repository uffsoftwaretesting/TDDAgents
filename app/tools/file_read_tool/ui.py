"""
How a ReadFile call is named and shown.

Ported from `reference/claude-code/src/tools/FileReadTool/UI.tsx` -> `userFacingName`,
`renderToolUseMessage`, `getToolUseSummary`, `getAgentOutputTaskId`; `FileReadTool.ts` ->
`getActivityDescription`. Not ported: the plan-file and agent-output names (`Reading Plan`,
`Read agent output`), which need the session's plan directory and task registry, and the
non-verbose `getDisplayPath` shortening, which needs the tracked cwd.
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.file import get_display_path


def user_facing_name(input: dict[str, Any] | None) -> str:
    return "Read"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    file_path = input.get("file_path")
    if not file_path:
        return None
    display = str(file_path)
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
