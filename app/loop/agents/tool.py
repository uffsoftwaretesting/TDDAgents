"""
The Agent tool (Phase 7): delegation to a worker that runs its own loop.

Ported from `reference/claude-code/src/tools/AgentTool/AgentTool.tsx` -> `AgentTool`
(`call`, `checkPermissions`, `isReadOnly`, `isConcurrencySafe`,
`mapToolResultToToolResultBlockParam`) and `agentToolUtils.ts` -> `finalizeAgentTool`,
`countToolUses`. The sync path only: background workers, teammates, worktree and remote
isolation arrive with Phase 8 or not at all, so `run_in_background`, `name`, `team_name`,
`mode`, `isolation` and `cwd` are absent from the schema — upstream omits them the same way
when their gates are off.

Where this diverges, each by decision:

* **`subagent_type` is required.** Upstream defaults an omitted type to `general-purpose`,
  or to the fork path when forking is enabled; forking is off in a headless session
  (`isForkSubagentEnabled`) and TDDAgents ships no general-purpose agent.
* **`model` accepts the Claude aliases and an OpenAI table** (`model_aliases.py`).
* **Phase-on-spawn (TDD).** Before the worker starts, the shared ledger moves to the
  agent's declared `phase` if `PhaseLedger.transition_to` allows it — GREEN needs a confirmed
  RED, REFACTOR a completed cycle, and RED restarts the cycle. A refused spawn raises before
  any worker runs, so the coordinator sees why. The observations themselves stay `RunTests`'
  alone: this write can only move the phase or erase evidence, never create it.

Errors raise, and `run_tool_use`'s outer handler turns them into the call's
`<tool_use_error>`, as upstream's `call` throws into its tool framework.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.loop.agents.definition import AgentDefinition
from app.loop.agents.prompt import get_prompt
from app.loop.agents.resolution import filter_denied_agents, get_deny_rule_for_agent
from app.loop.agents.run_agent import create_agent_id, run_agent
from app.loop.config import RunConfig
from app.loop.context import ToolContext
from app.loop.context.assembly import enhance_system_prompt_with_env_details
from app.loop.deps import LoopDeps
from app.loop.ledger import PhaseLedger
from app.loop.messages import Message, tool_calls_in
from app.loop.model_aliases import MODEL_ALIASES, get_agent_model
from app.loop.tools.base import BuiltTool, Tool, build_tool
from app.loop.tools.types import ToolResult

AGENT_TOOL_NAME = "Agent"
#: Upstream's `LEGACY_AGENT_TOOL_NAME`.
LEGACY_AGENT_TOOL_NAME = "Task"

#: `constants.ts` -> `ONE_SHOT_BUILTIN_AGENT_TYPES`: never continued, so no trailer.
ONE_SHOT_BUILTIN_AGENT_TYPES = frozenset({"Explore", "Plan"})

NO_OUTPUT_MARKER = "(Subagent completed but returned no output.)"

AGENT_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "description": "A short (3-5 word) description of the task"},
        "prompt": {"type": "string", "description": "The task for the agent to perform"},
        "subagent_type": {"type": "string", "description": "The type of specialized agent to use for this task"},
        "model": {
            "type": "string",
            "enum": sorted(MODEL_ALIASES),
            "description": (
                "Optional model override for this agent. Takes precedence over the agent definition's model "
                "frontmatter. If omitted, uses the agent definition's model, or inherits from the parent."
            ),
        },
    },
    "required": ["description", "prompt", "subagent_type"],
    "additionalProperties": False,
}


class AbortError(Exception):
    """`utils/errors.ts` -> `AbortError`: the worker was interrupted mid-run."""


@dataclass(frozen=True, slots=True)
class AgentToolResult:
    """`AgentToolResult`: what `finalizeAgentTool` reports (usage is not carried)."""

    agent_id: str
    agent_type: str
    content: tuple[str, ...]
    total_duration_ms: int
    total_tokens: int
    total_tool_use_count: int


def phase_on_spawn(ledger: PhaseLedger, agent: AgentDefinition) -> PhaseLedger:
    """The ledger a spawn of `agent` starts from; raises when the cycle forbids the spawn."""
    if agent.phase is None or agent.phase == ledger.phase:
        return ledger
    try:
        return ledger.transition_to(agent.phase)
    except ValueError as exc:
        raise ValueError(f"Agent '{agent.name}' cannot start: {exc}") from None


def _text_blocks(message: AIMessage) -> tuple[str, ...]:
    content = message.content
    if isinstance(content, str):
        return (content,) if content else ()
    return tuple(
        str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text"
    )


def _token_count(message: AIMessage) -> int:
    """`getTokenCountFromUsage`: LangChain's input count already includes the cache."""
    usage = message.usage_metadata
    return int(usage["input_tokens"]) + int(usage["output_tokens"]) if usage else 0


def finalize_agent_tool(
    agent_messages: Sequence[Message], agent_id: str, agent_type: str, start_time: float, *, now: float
) -> AgentToolResult:
    """`finalizeAgentTool`: the last assistant text, or the most recent text before it."""
    assistants = [m for m in agent_messages if isinstance(m, AIMessage)]
    if not assistants:
        raise ValueError("No assistant messages found")
    last = assistants[-1]
    # The last assistant's text, else the most recent text before it (the walk starts at `last`).
    content = next((blocks for m in reversed(assistants) if (blocks := _text_blocks(m))), ())
    return AgentToolResult(
        agent_id=agent_id,
        agent_type=agent_type,
        content=content,
        total_duration_ms=round((now - start_time) * 1000),
        total_tokens=_token_count(last),
        total_tool_use_count=sum(len(tool_calls_in(m)) for m in assistants),
    )


def map_agent_result(result: AgentToolResult) -> str:
    """`mapToolResultToToolResultBlockParam` for a completed sync agent, as one text."""
    content = "\n".join(result.content) if result.content else NO_OUTPUT_MARKER
    if result.agent_type in ONE_SHOT_BUILTIN_AGENT_TYPES:
        return content
    return (
        f"{content}\nagentId: {result.agent_id} (use SendMessage with to: '{result.agent_id}' to continue "
        f"this agent)\n<usage>total_tokens: {result.total_tokens}\ntool_uses: {result.total_tool_use_count}\n"
        f"duration_ms: {result.total_duration_ms}</usage>"
    )


def _select_agent(definitions: Mapping[str, AgentDefinition], context: ToolContext, agent_type: str) -> AgentDefinition:
    """`call`'s agent selection: deny rules first, then the lookup, with upstream's errors."""
    permission_context = context.get_app_state().tool_permission_context
    all_agents = list(definitions.values())
    agents = filter_denied_agents(all_agents, permission_context, AGENT_TOOL_NAME)
    found = next((a for a in agents if a.name == agent_type), None)
    if found is not None:
        return found
    if any(a.name == agent_type for a in all_agents):
        rule = get_deny_rule_for_agent(permission_context, AGENT_TOOL_NAME, agent_type)
        source = rule.source if rule is not None else "settings"
        raise ValueError(
            f"Agent type '{agent_type}' has been denied by permission rule '{AGENT_TOOL_NAME}({agent_type})' "
            f"from {source}."
        )
    raise ValueError(f"Agent type '{agent_type}' not found. Available agents: {', '.join(a.name for a in agents)}")


def _agent_prompt(agent: AgentDefinition, context: ToolContext) -> str:
    """`getSystemPrompt()` of a file agent: its body, plus its memory prompt when it has one."""
    if not agent.memory or agent.memory == "none":
        return agent.prompt
    store = context.agent_memory_store
    if store is None:
        raise ValueError("Run-scoped agent memory needs the run's memory store")
    memory = store.load_memory_prompt(agent.name, agent.memory, run_id=context.run_id or None)
    return f"{agent.prompt}\n\n{memory}"


def build_agent_tool(
    *,
    definitions: Mapping[str, AgentDefinition],
    worker_tools: Sequence[Tool],
    config: RunConfig,
    deps: LoopDeps,
    is_coordinator: bool = False,
    now: Callable[[], float] = time.time,
) -> BuiltTool:
    """
    The Agent tool for one run. `worker_tools` is upstream's `assembleToolPool` for workers —
    the full built-in roster, independent of the caller's own restrictions; `deps` are the
    loop dependencies each worker's `run_loop` is driven with.
    """

    async def call(args: dict[str, Any], context: ToolContext) -> ToolResult:
        start = now()
        agent = _select_agent(definitions, context, str(args["subagent_type"]))
        ledger = context.get_app_state().phase_ledger
        spawn_ledger = phase_on_spawn(ledger, agent)
        if spawn_ledger is not ledger:
            context.set_app_state(lambda s: replace(s, phase_ledger=spawn_ledger))

        model_override = args.get("model")
        model = get_agent_model(agent.model, config.model, model_override)
        additional = context.get_app_state().tool_permission_context.additional_working_directories
        system_prompt = enhance_system_prompt_with_env_details(
            [_agent_prompt(agent, context)], model, additional, context.workspace
        )
        agent_id = create_agent_id()
        messages: list[Message] = []
        error: Exception | None = None
        try:
            async for message in run_agent(
                agent_definition=agent,
                prompt_messages=[HumanMessage(content=str(args["prompt"]))],
                tool_context=context,
                config=config,
                deps=deps,
                available_tools=worker_tools,
                system_prompt=system_prompt,
                model_override=model_override,
                agent_id=agent_id,
            ):
                messages.append(message)
        except Exception as exc:  # noqa: BLE001 - upstream keeps partial progress (`syncAgentError`)
            error = exc
        if context.cancel.cancelled:
            raise AbortError()
        if error is not None and not any(isinstance(m, AIMessage) for m in messages):
            raise error
        result = finalize_agent_tool(messages, agent_id, agent.name, start, now=now())
        return ToolResult(
            content=map_agent_result(result),
            metadata={"agent_id": agent_id, "agent_type": agent.name, "status": "completed"},
        )

    return build_tool(
        name=AGENT_TOOL_NAME,
        prompt=get_prompt(list(definitions.values()), is_coordinator, default_agent_type=None),
        call=call,
        input_schema=AGENT_TOOL_SCHEMA,
        aliases=(LEGACY_AGENT_TOOL_NAME,),
        is_read_only=lambda args: True,
        is_concurrency_safe=lambda args: True,
        # `checkPermissions` auto-approves a spawn in the external build: the default allow.
        description=lambda args: str(args.get("description", "")),
    )


__all__ = [
    "AGENT_TOOL_NAME",
    "AGENT_TOOL_SCHEMA",
    "LEGACY_AGENT_TOOL_NAME",
    "NO_OUTPUT_MARKER",
    "ONE_SHOT_BUILTIN_AGENT_TYPES",
    "AbortError",
    "AgentToolResult",
    "build_agent_tool",
    "finalize_agent_tool",
    "map_agent_result",
    "phase_on_spawn",
]
