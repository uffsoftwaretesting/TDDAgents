"""
The Agent tool (Phase 7): delegation to a worker that runs its own loop.

Ported from `reference/claude-code/src/tools/AgentTool/AgentTool.tsx` -> `AgentTool`
(`call`, `checkPermissions`, `isReadOnly`, `isConcurrencySafe`,
`mapToolResultToToolResultBlockParam`) and `agentToolUtils.ts` -> `finalizeAgentTool`,
`countToolUses`. Teammates, worktree and remote isolation are not ported, so `name`,
`team_name`, `mode`, `isolation` and `cwd` are absent from the schema — upstream omits them the
same way when their gates are off.

**Background agents (Phase 8)** follow the async branch of `call`: `run_in_background`, a
definition's `background: true`, or coordinator mode (`isCoordinator`) start the worker as a
background task (`tasks/local_agent_task/local_agent_task.py`) and return at once; `TDDAGENTS_DISABLE_BACKGROUND_TASKS`
(upstream's `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`, read when the tool is built) removes the field
and runs everything in the foreground. **TDD:** only agents without a `phase` may run in the
background. An async worker's app-state writes are discarded upstream (`shareSetAppState:
!isAsync`), so a background tester could never confirm RED; asking for a phase agent in the
background is refused before anything runs, and coordinator mode's forced async applies to agents
without a phase only — forcing it on the TDD agents would make the cycle uncompletable.

Where this diverges, each by decision:

* **`subagent_type` is required.** Upstream defaults an omitted type to `general-purpose`,
  or to the fork path when forking is enabled; forking is off in a headless session
  (`isForkSubagentEnabled`) and TDDAgents ships no general-purpose agent.
* **`model` accepts the Claude aliases and an OpenAI table** (`utils/model/model.py`).
* **Phase-on-spawn (TDD, `app/tdd/phase_on_spawn.py`).** Before the worker starts, the shared ledger moves to the
  agent's declared `phase` if `PhaseLedger.transition_to` allows it — GREEN needs a confirmed
  RED, REFACTOR a completed cycle, and RED restarts the cycle. A refused spawn raises before
  any worker runs, so the coordinator sees why. The observations themselves stay `RunTests`'
  alone: this write can only move the phase or erase evidence, never create it.

Errors raise, and `run_tool_use`'s outer handler turns them into the call's
`<tool_use_error>`, as upstream's `call` throws into its tool framework.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.context import enhance_system_prompt_with_env_details
from app.query.config import RunConfig
from app.query.deps import LoopDeps
from app.tasks.local_agent_task.local_agent_task import (
    register_async_agent,
    run_async_agent_lifecycle,
    start_background_lifecycle,
)
from app.tdd.phase_on_spawn import phase_on_spawn, refuse_background_phase_agent
from app.tool import BuiltTool, CancelToken, Tool, ToolContext, ToolResult, build_tool
from app.tools.agent_tool.agent_tool_utils import (
    filter_denied_agents,
    get_deny_rule_for_agent,
)
from app.tools.agent_tool.load_agents_dir import AgentDefinition
from app.tools.agent_tool.prompt import get_prompt
from app.tools.agent_tool.run_agent import create_agent_id, run_agent
from app.utils.messages import Message, tool_calls_in
from app.utils.model.model import MODEL_ALIASES, get_agent_model
from app.utils.task.disk_output import get_task_output_path, init_task_output_as_symlink

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

#: The `run_in_background` field upstream adds unless background tasks are disabled.
RUN_IN_BACKGROUND_FIELD: dict[str, Any] = {
    "type": "boolean",
    "description": "Set to true to run this agent in the background. You will be notified when it completes.",
}

DISABLE_BACKGROUND_TASKS_ENV = "TDDAGENTS_DISABLE_BACKGROUND_TASKS"

FILE_READ_TOOL_NAME = "ReadFile"
BASH_TOOL_NAME = "Bash"


def is_background_tasks_disabled() -> bool:
    """`isBackgroundTasksDisabled`, read from the environment when called."""
    return os.environ.get(DISABLE_BACKGROUND_TASKS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def agent_tool_schema(background_enabled: bool) -> dict[str, Any]:
    """`inputSchema()`: the base fields, plus `run_in_background` when background tasks are on."""
    if not background_enabled:
        return AGENT_TOOL_SCHEMA
    properties = {**AGENT_TOOL_SCHEMA["properties"], "run_in_background": RUN_IN_BACKGROUND_FIELD}
    return {**AGENT_TOOL_SCHEMA, "properties": properties}


def map_async_launched(agent_id: str, output_file: str, can_read_output_file: bool) -> str:
    """`mapToolResultToToolResultBlockParam` for `async_launched`."""
    prefix = (
        f"Async agent launched successfully.\nagentId: {agent_id} (internal ID - do not mention to user. Use "
        f"SendMessage with to: '{agent_id}' to continue this agent.)\nThe agent is working in the background. "
        "You will be notified automatically when it completes."
    )
    if can_read_output_file:
        instructions = (
            "Do not duplicate this agent's work — avoid working with the same files or topics it is using. "
            "Work on non-overlapping tasks, or briefly tell the user what you launched and end your response."
            f"\noutput_file: {output_file}\nIf asked, you can check progress before completion by using "
            f"{FILE_READ_TOOL_NAME} or {BASH_TOOL_NAME} tail on the output file."
        )
    else:
        instructions = (
            "Briefly tell the user what you launched and end your response. Do not generate any other text — "
            "agent results will arrive in a subsequent message."
        )
    return f"{prefix}\n{instructions}"


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


def agent_system_prompt(agent: AgentDefinition, context: ToolContext) -> str:
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

    background_enabled = not is_background_tasks_disabled()

    async def call(args: dict[str, Any], context: ToolContext) -> ToolResult:
        start = now()
        agent = _select_agent(definitions, context, str(args["subagent_type"]))
        requested_background = bool(args.get("run_in_background")) or agent.background is True
        if background_enabled:
            refuse_background_phase_agent(agent, requested_background)
        run_async = background_enabled and agent.phase is None and (requested_background or is_coordinator)
        ledger = context.get_app_state().phase_ledger
        spawn_ledger = phase_on_spawn(ledger, agent)
        if spawn_ledger is not ledger:
            context.set_app_state(lambda s: replace(s, phase_ledger=spawn_ledger))

        model_override = args.get("model")
        model = get_agent_model(agent.model, config.model, model_override)
        additional = context.get_app_state().tool_permission_context.additional_working_directories
        system_prompt = enhance_system_prompt_with_env_details(
            [agent_system_prompt(agent, context)], model, additional, context.workspace
        )
        agent_id = create_agent_id()

        def worker(cancel: CancelToken | None = None) -> AsyncIterator[Message]:
            return run_agent(
                agent_definition=agent,
                prompt_messages=[HumanMessage(content=str(args["prompt"]))],
                tool_context=context,
                config=config,
                deps=deps,
                available_tools=worker_tools,
                system_prompt=system_prompt,
                is_async=run_async,
                model_override=model_override,
                agent_id=agent_id,
                cancel=cancel,
                description=str(args.get("description", "")) or None,
            )

        if run_async:
            return _launch_background(agent, agent_id, args, context, worker, start)

        messages: list[Message] = []
        error: Exception | None = None
        try:
            async for message in worker():
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

    def _launch_background(
        agent: AgentDefinition,
        agent_id: str,
        args: dict[str, Any],
        context: ToolContext,
        worker: Callable[[CancelToken | None], AsyncIterator[Message]],
        start: float,
    ) -> ToolResult:
        """`call`'s `shouldRunAsync` branch: register the task, start the lifecycle, return at once."""
        output_path = get_task_output_path(context, agent_id)
        store = context.transcript_store
        if store is not None:
            init_task_output_as_symlink(output_path, store.agent_transcript_path(agent_id))
        description = str(args.get("description", ""))
        task = register_async_agent(
            agent_id=agent_id, description=description, prompt=str(args["prompt"]), agent_type=agent.name,
            set_app_state=context.set_app_state, output_file=str(output_path), now=start,
            tool_use_id=context.tool_use_id,
        )
        start_background_lifecycle(run_async_agent_lifecycle(
            task_id=agent_id, cancel=task.cancel, stream=worker(task.cancel), description=description,
            agent_type=agent.name, start_time=start, root_set_app_state=context.set_app_state,
            queue=context.command_queue, output_file=str(output_path), now=now,
            tool_use_id=context.tool_use_id,
        ))
        can_read = any(t.name in (FILE_READ_TOOL_NAME, BASH_TOOL_NAME) for t in context.tools)
        return ToolResult(
            content=map_async_launched(agent_id, str(output_path), can_read),
            metadata={"agent_id": agent_id, "agent_type": agent.name, "status": "async_launched"},
        )

    return build_tool(
        name=AGENT_TOOL_NAME,
        prompt=get_prompt(list(definitions.values()), is_coordinator, default_agent_type=None,
                          background_tasks_enabled=background_enabled),
        call=call,
        input_schema=agent_tool_schema(background_enabled),
        aliases=(LEGACY_AGENT_TOOL_NAME,),
        is_read_only=lambda args: True,
        is_concurrency_safe=lambda args: True,
        # `checkPermissions` auto-approves a spawn in the external build: the default allow.
        description=lambda args: str(args.get("description", "")),
    )


__all__ = [
    "AGENT_TOOL_NAME",
    "AGENT_TOOL_SCHEMA",
    "DISABLE_BACKGROUND_TASKS_ENV",
    "LEGACY_AGENT_TOOL_NAME",
    "NO_OUTPUT_MARKER",
    "ONE_SHOT_BUILTIN_AGENT_TYPES",
    "RUN_IN_BACKGROUND_FIELD",
    "AbortError",
    "AgentToolResult",
    "agent_system_prompt",
    "agent_tool_schema",
    "build_agent_tool",
    "finalize_agent_tool",
    "is_background_tasks_disabled",
    "map_agent_result",
    "map_async_launched",
]
