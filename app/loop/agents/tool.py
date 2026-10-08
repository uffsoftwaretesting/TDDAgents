"""
The Agent delegation tool (I3).

Implements I3 conforming to §4.5:
- Conforms to Tool protocol (name="Agent", aliases=("Task",)).
- Assembles an independent worker tool pool via resolve_agent_tools & assemble_tool_pool.
- Supports sync execution (in-process) and async delegation (background task handle).
- Enforces TDD phase ledger invariants on agent definitions with declared phases.
- Manages run-scoped memory (I5) and forking (I4).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Mapping, Sequence
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from app.loop.agents.definition import AgentDefinition
from app.loop.agents.fork import (
    FORK_AGENT,
    FORK_SUBAGENT_TYPE,
    build_child_message,
    build_forked_messages,
    filter_incomplete_tool_calls,
    is_in_fork_child,
)
from app.loop.agents.loader import get_agent_definitions_with_overrides
from app.loop.agents.memory import AgentMemoryStore
from app.loop.agents.resolution import resolve_agent_tools
from app.loop.context import ToolContext, discard_app_state_update
from app.loop.messages import Message
from app.loop.tools.base import BuiltTool, Tool, build_tool
from app.loop.tools.pool import assemble_tool_pool
from app.loop.tools.types import ToolResult, ValidationResult

logger = logging.getLogger(__name__)

AGENT_TOOL_NAME = "Agent"
LEGACY_AGENT_TOOL_NAME = "Task"

AGENT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "The specific directive or task for the subagent to execute.",
        },
        "subagent_type": {
            "type": "string",
            "description": (
                "The type of subagent to invoke (e.g. 'developer', 'tester', 'refactorer', "
                "'explore', 'plan', 'verification'). If omitted, defaults to an implicit fork."
            ),
        },
        "run_in_background": {
            "type": "boolean",
            "description": "Run asynchronously in background and return task handle.",
            "default": False,
        },
        "isolation": {
            "type": "string",
            "enum": ["worktree", "same-dir"],
            "description": "Isolation mode for subagent execution.",
            "default": "same-dir",
        },
    },
    "required": ["prompt"],
}

SubagentRunner = Callable[
    [
        AgentDefinition,
        str,
        Sequence[Message],
        Sequence[Tool],
        ToolContext,
        str,
    ],
    Awaitable[tuple[str, str | None]],
]


async def default_subagent_runner(
    agent_def: AgentDefinition,
    directive: str,
    child_messages: Sequence[Message],
    worker_tools: Sequence[Tool],
    context: ToolContext,
    memory_prompt: str,
) -> tuple[str, str | None]:
    """
    Default deterministic subagent runner.

    Generates a structured fact report conforming to Claude Code format:
    Scope: <directive>
    Result: <summary of actions>
    Key files: <relevant files>
    Files changed: <modified files>
    Issues: <issues or blockers>
    """
    scope_line = directive.splitlines()[0] if directive else "Subagent task"
    summary = (
        f"Scope: {scope_line}\n"
        f"Result: Subagent '{agent_def.name}' completed task successfully.\n"
        f"Key files: []\n"
        f"Files changed: []\n"
        f"Issues: []"
    )
    return summary, "completed"


def build_agent_tool(
    available_tools: Sequence[Tool] = (),
    definitions: Mapping[str, AgentDefinition] | None = None,
    memory_store: AgentMemoryStore | None = None,
    subagent_runner: SubagentRunner | None = None,
    allow_nested_agent: bool = False,
    vars: Mapping[str, Any] | None = None,
) -> BuiltTool:
    """
    Build the Agent delegation tool conforming to the Tool protocol.
    """

    async def _call(input_dict: dict[str, Any], context: ToolContext) -> ToolResult:
        prompt = str(input_dict.get("prompt") or "").strip()
        subagent_type = input_dict.get("subagent_type")
        if isinstance(subagent_type, str):
            subagent_type = subagent_type.strip() or None
        else:
            subagent_type = None

        run_in_background = bool(input_dict.get("run_in_background", False))

        # Dynamic subagent instantiation conforming to Plan A
        try:
            from app.loop.agents.subagent import create_subagent

            target = subagent_type if subagent_type is not None else FORK_SUBAGENT_TYPE
            subagent = create_subagent(
                agent_name_or_def=target,
                parent_context=context,
                directive=prompt,
                vars=vars,
                available_tools=available_tools,
                definitions=definitions,
                memory_store=memory_store,
                subagent_runner=subagent_runner,
                allow_nested_agent=allow_nested_agent,
                run_in_background=run_in_background,
            )
        except ValueError as exc:
            err_msg = str(exc)
            if "requires phase" in err_msg:
                return ToolResult(content=f"Denied: {err_msg}", is_error=True)
            return ToolResult(content=f"Error: {err_msg}", is_error=True)

        # Async branch
        if run_in_background or subagent.definition.background is True:
            return ToolResult(
                content=f"Agent '{subagent.definition.name}' ({subagent.agent_id}) launched in background.",
                metadata={
                    "agent_id": subagent.agent_id,
                    "agent_type": subagent.definition.name,
                    "status": "async_launched",
                    "run_id": subagent.run_id,
                    "tools": tuple(t.name for t in subagent.tools),
                },
            )

        # Sync in-process execution
        try:
            summary, terminal_reason = await subagent.execute(prompt)
            return ToolResult(
                content=summary,
                metadata={
                    "agent_id": subagent.agent_id,
                    "agent_type": subagent.definition.name,
                    "status": "completed",
                    "terminal_reason": terminal_reason,
                    "run_id": subagent.run_id,
                },
            )
        finally:
            if memory_store is not None and subagent.definition.memory == "run":
                memory_store.discard_run_memory(subagent.run_id)

    def _validate(input_dict: dict[str, Any], context: ToolContext) -> ValidationResult:
        if not input_dict.get("prompt"):
            return ValidationResult(valid=False, message="Missing required parameter 'prompt'")
        return ValidationResult(valid=True)

    from app.loop.prompts.loader import render_prompt
    from app.loop.prompts.registry import global_prompt_registry

    prompt_text = (
        global_prompt_registry.get_tool_prompt("agent-usage-notes", vars)
        or "Spawn a subagent to perform focused work on a specific task."
    )
    if vars:
        prompt_text = render_prompt(prompt_text, vars)

    return build_tool(
        name=AGENT_TOOL_NAME,
        prompt=prompt_text,
        call=_call,
        input_schema=AGENT_TOOL_SCHEMA,
        aliases=(LEGACY_AGENT_TOOL_NAME,),
        validate_input=_validate,
    )
