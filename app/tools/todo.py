"""
TodoWrite — an in-process scratchpad for an agent's own plan.

The only tool that touches neither a workspace nor a network. The list lives on
`ToolContext.todos` and dies with the agent run, which is exactly the intent: it is a
thinking aid for one agent working through one sub-requirement, not a record anything
downstream reads. Nothing is written to disk, nothing enters the ledger, and nothing
reaches `workspace_output_*`.

Kept because a multi-step task the model can see the shape of goes better than one it is
holding entirely in its head — and because Phase 8 gets a free signal out of it: how often
an agent plans before acting, and whether that correlates with green on the first try.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.tools.base import (
    AnyTool,
    Capability,
    ToolContext,
    ToolResult,
    ValidationResult,
    build_tool,
    ok,
)

TodoStatus = Literal["pending", "in_progress", "completed"]

_MARKS: dict[str, str] = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}


class TodoItem(BaseModel):
    content: str = Field(description="What needs doing, in the imperative.")
    status: TodoStatus = Field(default="pending", description="pending, in_progress, or completed.")


class TodoWriteArgs(BaseModel):
    todos: list[TodoItem] = Field(description="The complete list, replacing any previous one.")


TODO_PROMPT = """Records your plan for the current task as a checklist.

Pass the complete list every time — it replaces the previous one rather than appending, so
updating an item means sending the whole list back with that item changed.

Keep exactly one item `in_progress` at a time, and mark it `completed` as soon as it is
done rather than in a batch at the end. Use this for work with several distinct steps;
skip it for anything you can finish in one or two actions."""


def _validate(args: TodoWriteArgs, ctx: ToolContext) -> ValidationResult:
    if not args.todos:
        return ValidationResult.invalid("todos must not be empty. Send at least one item.")

    in_progress = sum(1 for todo in args.todos if todo.status == "in_progress")
    if in_progress > 1:
        return ValidationResult.invalid(
            f"{in_progress} items are in_progress; keep exactly one at a time."
        )
    return ValidationResult()


def _todo_write(args: TodoWriteArgs, ctx: ToolContext) -> ToolResult:
    ctx.todos.clear()
    ctx.todos.extend({"content": todo.content, "status": todo.status} for todo in args.todos)

    rendered = "\n".join(f"{_MARKS[todo.status]} {todo.content}" for todo in args.todos)
    done = sum(1 for todo in args.todos if todo.status == "completed")
    summary = f"{done}/{len(args.todos)} complete"
    return ok(f"{rendered}\n\n{summary}")


TodoWrite = build_tool(
    name="TodoWrite",
    args_schema=TodoWriteArgs,
    prompt=TODO_PROMPT,
    call=_todo_write,
    description=lambda args: f"Update {len(args.todos)} todo(s)",
    # In-process only: it changes nothing outside this agent's own context, so it needs no
    # more permission than a read and cannot trigger a sync checkpoint.
    is_read_only=lambda args: True,
    required_capability=lambda args: Capability.READ,
    validate_input=_validate,
)

TODO_TOOLS: list[AnyTool] = [TodoWrite]
