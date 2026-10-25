"""
`TaskOutput` (Phase 8).

Ported from `reference/claude-code/src/tools/TaskOutputTool/TaskOutputTool.tsx` ->
`TaskOutputTool` (`validateInput`, `call`, `getTaskOutputData`, `waitForTaskCompletion`,
`mapToolResultToToolResultBlockParam`). Enabled, as in upstream's external build. Only the
`local_agent` task type exists here, so the shell and remote branches of `getTaskOutputData`
have no subject. The block/timeout bounds (`max(600000)`, default 30000) are upstream's own
schema and the 100 ms poll is upstream's `sleep(100)`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.loop.agents.tool import AbortError
from app.loop.context import ToolContext
from app.loop.prompts.loader import parse_markdown_frontmatter
from app.loop.tasks.disk_output import format_task_output, get_task_output
from app.loop.tasks.framework import update_task_state
from app.loop.tools.base import BuiltTool, build_tool
from app.loop.tools.types import ToolResult, ValidationResult

TASK_OUTPUT_TOOL_NAME = "TaskOutput"

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "tools" / "task_output.md"

#: `waitForTaskCompletion`'s poll interval, seconds.
POLL_INTERVAL_S = 0.1

TASK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "description": "The task ID to get output from"},
        "block": {"type": "boolean", "default": True, "description": "Whether to wait for completion"},
        "timeout": {"type": "number", "minimum": 0, "maximum": 600000, "default": 30000,
                    "description": "Max wait time in ms"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def _task_output_data(task: Any) -> dict[str, Any]:
    """
    `getTaskOutputData` for a local agent: its result, else the output file's tail. Upstream's
    `description` field feeds only its UI, never the model's result block, so it is left out.
    """
    output = get_task_output(Path(task.output_file))
    clean = getattr(task, "result", None)
    return {
        "task_id": task.id,
        "task_type": task.type,
        "status": task.status,
        "output": clean or output,
        "error": getattr(task, "error", None),
        "output_file": task.output_file,
    }


async def _wait_for_task_completion(task_id: str, context: ToolContext, timeout_ms: float) -> Any:
    """`waitForTaskCompletion`: the task once it stops running, or as it stands at the timeout."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if context.cancel.cancelled:
            raise AbortError()
        task = context.get_app_state().tasks.get(task_id)
        if task is None:
            return None
        if task.status not in ("running", "pending"):
            return task
        await asyncio.sleep(POLL_INTERVAL_S)
    return context.get_app_state().tasks.get(task_id)


def _render(status: str, task: dict[str, Any] | None) -> str:
    """`mapToolResultToToolResultBlockParam`."""
    parts = [f"<retrieval_status>{status}</retrieval_status>"]
    if task is not None:
        parts += [f"<task_id>{task['task_id']}</task_id>", f"<task_type>{task['task_type']}</task_type>",
                  f"<status>{task['status']}</status>"]
        if task["output"].strip():
            content, _ = format_task_output(task["output"], task["output_file"])
            parts.append(f"<output>\n{content.rstrip()}\n</output>")
        if task["error"]:
            parts.append(f"<error>{task['error']}</error>")
    return "\n\n".join(parts)


def _mark_notified(task_id: str, context: ToolContext) -> None:
    update_task_state(task_id, context.set_app_state, lambda t: replace(t, notified=True))


def build_task_output_tool() -> BuiltTool:
    """The `TaskOutput` tool."""

    def validate(args: dict[str, Any], context: ToolContext) -> ValidationResult:
        task_id = args.get("task_id")
        if not task_id:
            return ValidationResult(valid=False, message="Task ID is required", error_code=1)
        if context.get_app_state().tasks.get(task_id) is None:
            return ValidationResult(valid=False, message=f"No task found with ID: {task_id}", error_code=2)
        return ValidationResult(valid=True)

    async def call(args: dict[str, Any], context: ToolContext) -> ToolResult:
        task_id = str(args["task_id"])
        task = context.get_app_state().tasks.get(task_id)
        if task is None:
            raise ValueError(f"No task found with ID: {task_id}")
        if not args.get("block", True):
            if task.status not in ("running", "pending"):
                _mark_notified(task_id, context)
                return ToolResult(content=_render("success", _task_output_data(task)))
            return ToolResult(content=_render("not_ready", _task_output_data(task)))
        done = await _wait_for_task_completion(task_id, context, float(args.get("timeout", 30000)))
        if done is None:
            return ToolResult(content=_render("timeout", None))
        if done.status in ("running", "pending"):
            return ToolResult(content=_render("timeout", _task_output_data(done)))
        _mark_notified(task_id, context)
        return ToolResult(content=_render("success", _task_output_data(done)))

    return build_tool(
        name=TASK_OUTPUT_TOOL_NAME,
        prompt=parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1].strip(),
        call=call,
        input_schema=TASK_OUTPUT_SCHEMA,
        description=lambda args: "[Deprecated] — prefer Read on the task output file path",
        aliases=("AgentOutputTool", "BashOutputTool"),
        is_read_only=lambda args: True,
        is_concurrency_safe=lambda args: True,
        validate_input=validate,
        max_result_size_chars=100_000,
    )


__all__ = ["TASK_OUTPUT_SCHEMA", "TASK_OUTPUT_TOOL_NAME", "build_task_output_tool"]
