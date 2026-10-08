"""
Dynamic Subagent Instantiation and Orchestration (Plan A).

Ported from:
- `reference/claude-code/src/tools/AgentTool/AgentTool.ts`
- `reference/claude-code/src/tools/AgentTool/runAgent.ts`
- `docs/refactoring_transition_plan.md` -> Plan A (Subagent Orchestration & Delegation)

Provides:
- Dynamic instantiation of subagents loaded from PromptRegistry or filesystem.
- Context isolation boundary via `discard_app_state_update`.
- TDD phase validation against parent ledger.
- Per-agent tool resolution and worker pool assembly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
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
from app.loop.agents.loader import (
    get_agent_definitions_with_overrides,
    load_agent_definition,
    load_agent_definition_from_path,
)
from app.loop.agents.memory import AgentMemoryStore
from app.loop.agents.resolution import resolve_agent_tools
from app.loop.context import ToolContext, discard_app_state_update
from app.loop.messages import Message
from app.loop.tools.base import Tool
from app.loop.tools.pool import assemble_tool_pool

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SubagentInstance:
    """An instantiated subagent ready for execution."""

    definition: AgentDefinition
    context: ToolContext
    tools: tuple[Tool, ...]
    run_id: str
    agent_id: str
    memory_prompt: str
    runner: Any

    async def execute(self, prompt: str) -> tuple[str, str | None]:
        """Execute the subagent with the given prompt directive."""
        child_messages = list(self.context.messages)
        if not any(isinstance(m, HumanMessage) for m in child_messages):
            child_messages.append(HumanMessage(content=prompt))
        return await self.runner(
            self.definition,
            prompt,
            child_messages,
            self.tools,
            self.context,
            self.memory_prompt,
        )


def _resolve_agent_definition(
    agent_name_or_def: str | AgentDefinition,
    parent_context: ToolContext,
    definitions: Mapping[str, AgentDefinition] | None = None,
    vars: Mapping[str, Any] | None = None,
) -> AgentDefinition:
    """Resolve an AgentDefinition from object, definitions mapping, registry, or filesystem."""
    if isinstance(agent_name_or_def, AgentDefinition):
        return agent_name_or_def

    name = str(agent_name_or_def).strip()
    if not name or name == FORK_SUBAGENT_TYPE:
        return FORK_AGENT

    # 1. Check explicitly passed definitions mapping
    if definitions is not None:
        if name in definitions:
            return definitions[name]
        raise ValueError(
            f"Unknown agent type '{name}'. Available: {sorted(definitions.keys())}"
        )

    # 2. Check global PromptRegistry (all 81 agent prompts)
    from app.loop.prompts.registry import global_prompt_registry

    entry = global_prompt_registry.find_prompt("agent-prompts", name)
    if entry is not None:
        filepath = entry.get("filepath")
        if filepath and Path(filepath).is_file():
            try:
                return load_agent_definition_from_path(filepath, vars=vars, source="registry")
            except Exception as e:
                logger.warning("Failed to load agent definition from path %s: %s", filepath, e)
        # Fallback to in-memory frontmatter/body
        fm = entry.get("frontmatter", {})
        body = entry.get("body", "")
        import yaml
        content = f"---\n{yaml.dump(fm)}---\n{body}"
        return load_agent_definition(content, vars=vars, source="registry")

    # 3. Check filesystem overrides (built-in, user, project)
    workspace_root = getattr(parent_context.workspace, "root", None)
    discovered = get_agent_definitions_with_overrides(project_dir=workspace_root, vars=vars)
    if name in discovered:
        return discovered[name]

    raise ValueError(
        f"Unknown agent type '{name}'. "
        f"Available in registry: {len(global_prompt_registry.list_prompts('agent-prompts'))} prompts."
    )


def create_subagent(
    agent_name_or_def: str | AgentDefinition,
    parent_context: ToolContext,
    *,
    directive: str = "",
    vars: Mapping[str, Any] | None = None,
    available_tools: Sequence[Tool] = (),
    definitions: Mapping[str, AgentDefinition] | None = None,
    memory_store: AgentMemoryStore | None = None,
    subagent_runner: Any = None,
    allow_nested_agent: bool = False,
    run_in_background: bool = False,
) -> SubagentInstance:
    """
    Dynamically instantiate a subagent conforming to Plan A.

    - Resolves definition from registry or overrides with variable substitution.
    - Validates TDD phase invariants against parent ledger.
    - Isolates context boundary: ToolContext receives `discard_app_state_update`.
    - Filters tools and assembles independent worker tool pool.
    """
    # Check fork recursion guard
    if (
        (isinstance(agent_name_or_def, str) and not agent_name_or_def)
        or agent_name_or_def == FORK_SUBAGENT_TYPE
        or agent_name_or_def is FORK_AGENT
    ):
        if is_in_fork_child(parent_context.messages):
            raise ValueError("Fork children cannot recursively spawn fork subagents.")

    # 1. Resolve agent definition
    agent_def = _resolve_agent_definition(
        agent_name_or_def, parent_context, definitions=definitions, vars=vars
    )

    # 2. Enforce TDD phase invariant
    current_app_state = parent_context.get_app_state()
    current_ledger = current_app_state.phase_ledger
    if agent_def.phase is not None and current_ledger is not None:
        if current_ledger.phase != agent_def.phase:
            raise ValueError(
                f"Agent '{agent_def.name}' requires phase {agent_def.phase.value}, "
                f"but current TDD ledger is in phase {current_ledger.phase.value}."
            )

    # 3. Resolve worker tools
    candidate_pool = list(available_tools) if available_tools else list(parent_context.tools)
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

    # 4. Context & message preparation
    if agent_def.name == FORK_SUBAGENT_TYPE:
        last_assistant = next(
            (
                m for m in reversed(parent_context.messages)
                if getattr(m, "type", "") == "assistant" or isinstance(m, AIMessage)
            ),
            None,
        )
        if last_assistant is not None:
            prior_history = [m for m in parent_context.messages if m is not last_assistant]
            filtered_prior = filter_incomplete_tool_calls(prior_history)
            child_messages = build_forked_messages(directive, last_assistant, filtered_prior)
        else:
            child_messages = [HumanMessage(content=build_child_message(directive))]
    else:
        child_messages = [HumanMessage(content=directive)] if directive else []

    # 5. Agent memory setup
    run_id = f"run_{uuid4().hex[:8]}"
    agent_id = f"agent_{uuid4().hex[:8]}"
    store = memory_store or AgentMemoryStore(base_dir=getattr(parent_context.workspace, "root", None))
    memory_prompt = ""
    if agent_def.memory and agent_def.memory != "none":
        memory_prompt = store.load_memory_prompt(agent_def.name, agent_def.memory, run_id=run_id)

    # 6. Isolated ToolContext
    worker_context = ToolContext(
        cancel=parent_context.cancel,
        get_app_state=parent_context.get_app_state,
        set_app_state=discard_app_state_update,  # Load-bearing isolation boundary!
        messages=tuple(child_messages),
        tools=tuple(worker_pool),
        permission_context=parent_context.permission_context,
        workspace=parent_context.workspace,
        hook_dispatcher=parent_context.hook_dispatcher,
        read_file_state=dict(parent_context.read_file_state),
    )

    from app.loop.agents.tool import default_subagent_runner
    runner = subagent_runner or default_subagent_runner

    return SubagentInstance(
        definition=agent_def,
        context=worker_context,
        tools=tuple(worker_pool),
        run_id=run_id,
        agent_id=agent_id,
        memory_prompt=memory_prompt,
        runner=runner,
    )
