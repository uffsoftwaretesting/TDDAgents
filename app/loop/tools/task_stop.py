"""
`TaskStop` (Phase 8).

Ported from `reference/claude-code/src/tools/TaskStopTool/TaskStopTool.ts` -> `TaskStopTool`
and `prompt.ts`: stop a running background task by id. Upstream's `shouldDefer` (deferred tool
search) has no subject here; the tool is always in the main thread's pool and disallowed for
workers (`ALL_AGENT_DISALLOWED_TOOLS`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.loop.context.tool_context import ToolContext
from app.loop.prompts.loader import parse_markdown_frontmatter
from app.loop.tasks.stop_task import stop_task
from app.loop.tools.base import BuiltTool, build_tool
from app.loop.tools.types import ToolResult, ValidationResult

TASK_STOP_TOOL_NAME = "TaskStop"

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "tools" / "task_stop.md"

TASK_STOP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "description": "The ID of the background task to stop"},
        "shell_id": {"type": "string", "description": "Deprecated: use task_id instead"},
    },
    "additionalProperties": False,
}


def _prompt() -> str:
    """`DESCRIPTION`, kept with upstream's leading and trailing newline."""
    return "\n" + parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1].strip() + "\n"


def _task_id(args: dict[str, Any]) -> str | None:
    return args.get("task_id") or args.get("shell_id")


def build_task_stop_tool() -> BuiltTool:
    """The `TaskStop` tool."""

    def validate(args: dict[str, Any], context: ToolContext) -> ValidationResult:
        task_id = _task_id(args)
        if not task_id:
            return ValidationResult(valid=False, message="Missing required parameter: task_id", error_code=1)
        task = context.get_app_state().tasks.get(task_id)
        if task is None:
            return ValidationResult(valid=False, message=f"No task found with ID: {task_id}", error_code=1)
        if task.status != "running":
            return ValidationResult(
                valid=False, message=f"Task {task_id} is not running (status: {task.status})", error_code=3,
            )
        return ValidationResult(valid=True)

    async def call(args: dict[str, Any], context: ToolContext) -> ToolResult:
        task_id = _task_id(args)
        if not task_id:
            raise ValueError("Missing required parameter: task_id")
        result = await stop_task(task_id, context.get_app_state, context.set_app_state)
        output = {
            "message": f"Successfully stopped task: {result.task_id} ({result.command})",
            "task_id": result.task_id,
            "task_type": result.task_type,
            "command": result.command,
        }
        return ToolResult(content=json.dumps(output, separators=(",", ":"), ensure_ascii=False))

    return build_tool(
        name=TASK_STOP_TOOL_NAME,
        prompt=_prompt(),
        call=call,
        input_schema=TASK_STOP_SCHEMA,
        description=lambda args: "Stop a running background task by ID",
        aliases=("KillShell",),
        is_concurrency_safe=lambda args: True,
        validate_input=validate,
        max_result_size_chars=100_000,
    )


__all__ = ["TASK_STOP_SCHEMA", "TASK_STOP_TOOL_NAME", "build_task_stop_tool"]
