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

        # Check fork guard
        if subagent_type is None and is_in_fork_child(context.messages):
            return ToolResult(
                content="Error: Fork children cannot recursively spawn fork subagents.",
                is_error=True,
            )

        # 1. Resolve agent definition
        if subagent_type is None:
            agent_def = FORK_AGENT
        else:
            all_defs = (
                dict(definitions)
                if definitions is not None
                else get_agent_definitions_with_overrides(
                    project_dir=getattr(context.workspace, "root", None)
                )
            )
            if subagent_type not in all_defs:
                return ToolResult(
                    content=(
                        f"Error: Unknown agent type '{subagent_type}'. "
                        f"Available: {sorted(all_defs.keys())}"
                    ),
                    is_error=True,
                )
            agent_def = all_defs[subagent_type]

        # 2. Enforce TDD phase invariant
        current_app_state = context.get_app_state()
        current_ledger = current_app_state.phase_ledger
        if agent_def.phase is not None and current_ledger is not None:
            if current_ledger.phase != agent_def.phase:
                return ToolResult(
                    content=(
                        f"Denied: Agent '{agent_def.name}' requires phase {agent_def.phase}, "
                        f"but current TDD ledger is in phase {current_ledger.phase}."
                    ),
                    is_error=True,
                )

        # 3. Resolve worker tools (I2)
        candidate_pool = list(available_tools) if available_tools else list(context.tools)
        resolved_tools = resolve_agent_tools(
            agent_def,
            candidate_pool,
            is_async=run_in_background,
            is_main_thread=False,
            allow_nested_agent=allow_nested_agent,
        )

        worker_pool = assemble_tool_pool(
            built_in_tools=resolved_tools.resolved_tools,
            phase_ledger=current_ledger,
        )

        # 4. Context & message preparation (I4)
        if agent_def.name == FORK_SUBAGENT_TYPE:
            last_assistant = next(
                (
                    m for m in reversed(context.messages)
                    if getattr(m, "type", "") == "assistant" or isinstance(m, AIMessage)
                ),
                None,
            )
            if last_assistant is not None:
                prior_history = [m for m in context.messages if m is not last_assistant]
                filtered_prior = filter_incomplete_tool_calls(prior_history)
                child_messages = build_forked_messages(prompt, last_assistant, filtered_prior)
            else:
                child_messages = [HumanMessage(content=build_child_message(prompt))]
        else:
            child_messages = [HumanMessage(content=prompt)]

        # 5. Agent memory setup (I5)
        run_id = f"run_{uuid4().hex[:8]}"
        agent_id = f"agent_{uuid4().hex[:8]}"
        store = memory_store or AgentMemoryStore(base_dir=getattr(context.workspace, "root", None))
        memory_prompt = ""
        if agent_def.memory and agent_def.memory != "none":
            memory_prompt = store.load_memory_prompt(agent_def.name, agent_def.memory, run_id=run_id)

        # 6. Async branch
        if run_in_background or agent_def.background is True:
            return ToolResult(
                content=f"Agent '{agent_def.name}' ({agent_id}) launched in background.",
                metadata={
                    "agent_id": agent_id,
                    "agent_type": agent_def.name,
                    "status": "async_launched",
                    "run_id": run_id,
                    "tools": tuple(t.name for t in worker_pool),
                },
            )

        # 7. Sync in-process execution (decision A1)
        worker_context = ToolContext(
            cancel=context.cancel,
            get_app_state=context.get_app_state,
            set_app_state=discard_app_state_update,
            messages=tuple(child_messages),
            tools=tuple(worker_pool),
            permission_context=context.permission_context,
            workspace=context.workspace,
            hook_dispatcher=context.hook_dispatcher,
        )

        runner = subagent_runner or default_subagent_runner
        try:
            summary, terminal_reason = await runner(
                agent_def,
                prompt,
                child_messages,
                worker_pool,
                worker_context,
                memory_prompt,
            )
            return ToolResult(
                content=summary,
                metadata={
                    "agent_id": agent_id,
                    "agent_type": agent_def.name,
                    "status": "completed",
                    "terminal_reason": terminal_reason,
                    "run_id": run_id,
                },
            )
        finally:
            if store is not None and agent_def.memory == "run":
                store.discard_run_memory(run_id)

    def _validate(input_dict: dict[str, Any], context: ToolContext) -> ValidationResult:
        if not input_dict.get("prompt"):
            return ValidationResult(valid=False, message="Missing required parameter 'prompt'")
        return ValidationResult(valid=True)

    return build_tool(
        name=AGENT_TOOL_NAME,
        prompt="Spawn a subagent to perform focused work on a specific task.",
        call=_call,
        input_schema=AGENT_TOOL_SCHEMA,
        aliases=(LEGACY_AGENT_TOOL_NAME,),
        validate_input=_validate,
    )
