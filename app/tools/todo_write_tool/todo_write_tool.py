"""
The `TodoWrite` tool (Phase 2).

Ported from `reference/claude-code/src/tools/TodoWriteTool/TodoWriteTool.ts` and
`utils/todo/types.ts`. The schema is upstream's, down to the two details that are easy to
drop and both change behaviour:

* **The top level is strict** — an unknown key is rejected, which is how the model learns it
  invented a parameter. The item objects are *not* strict, so an extra key on a todo is
  ignored rather than failing the call.
* **Every item carries both forms.** `content` is imperative ("Write the failing test") and
  `activeForm` present continuous ("Writing the failing test"), so a display can say what is
  happening without rewriting the model's words.

The list lives in `AppState`, keyed by agent, so a subagent's todos never overwrite the
coordinator's. An all-complete list clears the stored value while still returning the full
list — upstream does the same: the record of what was done belongs to the result, the state
is only what is still outstanding.

This replaces the ad-hoc `TODO.md` read in `model.py`: a file in the workspace was both
visible to the agent as ordinary content and indistinguishable from a file the project owns.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.tool import BuiltTool, ToolContext, ToolResult, build_tool
from app.utils.frontmatter_parser import parse_markdown_frontmatter

TODO_WRITE_TOOL_NAME = "TodoWrite"

PROMPT_PATH = Path(__file__).with_name("prompt.md")
TODO_WRITE_PROMPT = (
    parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1]
    if PROMPT_PATH.exists()
    else "Update the todo list for the current session."
)

#: `utils/todo/types.ts` -> `TodoStatusSchema`.
TODO_STATUSES = ("pending", "in_progress", "completed")

#: `TodoWriteTool.ts` -> `inputSchema`, as JSON Schema.
TODO_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "description": "The updated todo list",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "minLength": 1,
                                "description": "Imperative form, e.g. 'Write the failing test'"},
                    "status": {"type": "string", "enum": list(TODO_STATUSES)},
                    "activeForm": {"type": "string", "minLength": 1,
                                   "description": "Present continuous form, e.g. 'Writing the failing test'"},
                },
                "required": ["content", "status", "activeForm"],
            },
        }
    },
    "required": ["todos"],
    "additionalProperties": False,
}

#: `TodoWriteTool.ts` -> `mapToolResultToToolResultBlockParam`, verbatim.
TODO_RESULT_MESSAGE = (
    "Todos have been modified successfully. Ensure that you continue to use the todo list "
    "to track your progress. Please proceed with the current tasks if applicable"
)


@dataclass(frozen=True, slots=True)
class TodoItem:
    content: str
    status: str
    active_form: str

    @classmethod
    def from_input(cls, raw: Mapping[str, Any]) -> TodoItem:
        return cls(
            content=str(raw.get("content", "")),
            status=str(raw.get("status", "pending")),
            active_form=str(raw.get("activeForm", "")),
        )

    def to_dict(self) -> dict[str, str]:
        return {"content": self.content, "status": self.status, "activeForm": self.active_form}


def read_todos(context: ToolContext) -> tuple[TodoItem, ...]:
    """The current list for this agent, or an empty one."""
    state = context.get_app_state()
    todos = getattr(state, "todos", None)
    if not isinstance(todos, Mapping):
        return ()
    stored = todos.get(_agent_key(context))
    return tuple(stored) if isinstance(stored, tuple) else ()


def _agent_key(context: ToolContext) -> str:
    """`context.agentId ?? getSessionId()`: a subagent keeps its own list."""
    return str(getattr(context, "agent_id", None) or "main")


def build_todo_write_tool() -> BuiltTool:
    async def call_todo_write(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        raw = input_args.get("todos") or []
        todos = tuple(TodoItem.from_input(item) for item in raw if isinstance(item, Mapping))
        all_done = bool(todos) and all(item.status == "completed" for item in todos)
        key = _agent_key(context)

        def store(state: Any) -> Any:
            existing = getattr(state, "todos", None)
            merged = dict(existing) if isinstance(existing, Mapping) else {}
            # An all-complete list is cleared from state while the result still reports it:
            # what was finished belongs to the transcript, state holds what is outstanding.
            merged[key] = () if all_done else todos
            return dataclasses.replace(state, todos=merged)

        try:
            context.set_app_state(store)
        except TypeError:
            # An AppState without a `todos` field: the tool is informational, not a reason
            # to fail the turn.
            pass

        return ToolResult(content=TODO_RESULT_MESSAGE)

    return build_tool(
        name=TODO_WRITE_TOOL_NAME,
        prompt=TODO_WRITE_PROMPT,
        input_schema=TODO_INPUT_SCHEMA,
        description=lambda args: f"Update {len(args.get('todos') or [])} todo items",
        # Upstream declares neither predicate, so `TOOL_DEFAULTS` makes both false: it is
        # never read-only (it writes state) and never concurrency-safe (two writers would
        # race on one list).
        call=call_todo_write,
    )


TodoWrite = build_todo_write_tool()

__all__ = [
    "TODO_INPUT_SCHEMA",
    "TODO_RESULT_MESSAGE",
    "TODO_STATUSES",
    "TODO_WRITE_PROMPT",
    "TODO_WRITE_TOOL_NAME",
    "TodoItem",
    "TodoWrite",
    "build_todo_write_tool",
    "read_todos",
]
