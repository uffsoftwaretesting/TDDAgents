"""
One worker's run (Phase 7): the port of `runAgent`.

Ported from `reference/claude-code/src/tools/AgentTool/runAgent.ts` -> `runAgent`, with the
context construction of `utils/forkedAgent.ts` -> `createSubagentContext`. A worker is a
second `run_loop` over a fresh message list (its prompt, plus any SubagentStart context),
with its own system prompt, its own resolved tool pool and its own identity. What it shares
with its parent is decided exactly as upstream decides it:

* **App state.** A synchronous worker writes through its parent's `set_app_state`
  (`shareSetAppState: !isAsync`). That is what lets a tester's `RunTests` end RED on the
  coordinator's ledger — the ledger *is* app state. An asynchronous worker's writes are
  discarded except its task state, which reaches the root store (upstream's
  `setAppStateForTasks`), and it keeps its own denial tally.
* **Permission mode.** The definition's `permissionMode` applies unless the parent runs in
  `bypassPermissions`, `acceptEdits` or `auto`; an asynchronous worker never prompts.
* **Abort.** A synchronous worker shares its parent's cancel token; an asynchronous one gets
  its own (Phase 8), so it survives the main thread being cancelled and is stopped by
  `TaskStop` instead.

Two TDDAgents additions keep the TDD barriers inside the worker: the resolved pool is
filtered by the live phase ledger (barrier 2, `assemble_tool_pool`), re-derived between
turns when the phase moves; and the worker's transitions never reach the parent's event
sink — sidechains are excluded from the sub-requirement's flow (Part L). The worker's
`Stop` hooks fire as `SubagentStop`, because its context carries an `agent_id`.

`maxTurns` is upstream's own optional ceiling and applies only when a definition declares it:
the worker ends before the turn past the limit is sent, where upstream yields its
`max_turns_reached` attachment and stops.

Preloaded skills (`skillsToPreload`) are resolved against the run's shared skill registry
and appended after the SubagentStart context, as upstream appends them.

Not ported: MCP servers per agent, frontmatter hooks (no session-hook
store here), effort, the fork path's exact-tools prefix, Perfetto tracing, agent metadata
files, and the shell/monitor task kill (no background shells or monitors here).

Skill preloading for agents (Phase 8).

Ported from `reference/claude-code/src/tools/AgentTool/runAgent.ts` -> the `skillsToPreload`
loop and `resolveSkillName`. Each skill an agent's frontmatter names (`skills:`) is loaded into
the worker's first messages as one `isMeta` user message: the skill-format loading metadata,
then the skill's text with no arguments. A name that resolves to nothing is logged and skipped.

Skills come from `getSkillToolCommands` — the ones the model may invoke — so a skill with
`disable-model-invocation` cannot be preloaded either.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import replace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.query.config import RunConfig
from app.query.deps import LoopDeps
from app.query.query import run_loop
from app.query.state import initial_loop_state
from app.query.transitions import Continue, Terminated
from app.services.compact.compact import is_compact_boundary_message
from app.skills.load_skills_dir import SkillCommand, SkillRegistry
from app.state.app_state_store import AppState
from app.tool import CancelToken, DenialTrackingBox, Tool, ToolContext
from app.tools.agent_tool.agent_tool_utils import resolve_agent_tools
from app.tools.agent_tool.load_agents_dir import AgentDefinition
from app.tools.skill_tool.skill_tool import format_skill_loading_metadata
from app.tools.tools import assemble_tool_pool
from app.types.permissions import PermissionMode
from app.utils.hooks.hooks import execute_subagent_start_hooks
from app.utils.messages import HookAttachment, Message, create_attachment_message
from app.utils.model.model import get_agent_model

logger = logging.getLogger(__name__)

#: Parent modes a definition's `permissionMode` never overrides.
_PERMISSIVE_PARENT_MODES = frozenset({
    PermissionMode.BYPASS_PERMISSIONS, PermissionMode.ACCEPT_EDITS, PermissionMode.AUTO,
})

#: Upstream drops `gitStatus` from these agents' system context.
_NO_GIT_STATUS_AGENT_TYPES = frozenset({"Explore", "Plan"})


class _MaxTurnsReached(Exception):
    """Raised from the worker's event sink to end the run before the turn past `maxTurns`."""


def create_agent_id(label: str | None = None) -> str:
    """`utils/uuid.ts` -> `createAgentId`: `a` + 16 hex digits, or `a<label>-<hex>`."""
    suffix = secrets.token_hex(8)
    return f"a{label}-{suffix}" if label else f"a{suffix}"


def _agent_get_app_state(
    parent_get: Callable[[], AppState], permission_mode: str | None, is_async: bool
) -> Callable[[], AppState]:
    """`runAgent`'s `agentGetAppState`: the definition's mode and the no-prompt flag."""

    def get() -> AppState:
        state = parent_get()
        context = state.tool_permission_context
        if permission_mode and context.mode not in _PERMISSIVE_PARENT_MODES:
            context = replace(context, mode=PermissionMode(permission_mode))
        if is_async:
            context = replace(context, should_avoid_permission_prompts=True)
        if context is state.tool_permission_context:
            return state
        return replace(state, tool_permission_context=context)

    return get


def _task_state_only(root: Callable[[Callable[[AppState], AppState]], None]) -> Callable[..., None]:
    """
    A background worker's `set_app_state`: upstream gives it a no-op `setAppState` and the root
    `setAppStateForTasks`; here one setter does both — the `tasks` an updater produces reach the
    root store, and every other change (the ledger, todos) is discarded.
    """

    def set_tasks(updater: Callable[[AppState], AppState]) -> None:
        def apply(state: AppState) -> AppState:
            updated = updater(state)
            return state if updated.tasks is state.tasks else replace(state, tasks=updated.tasks)

        root(apply)

    return set_tasks


def _write_agent_metadata(store: Any, agent_id: str, agent_type: str, description: str | None) -> None:
    """`writeAgentMetadata`, fire-and-forget as upstream's `void ... .catch(log)`."""
    try:
        extra = {"description": description} if description else {}
        store.write_agent_metadata(agent_id, {"agentType": agent_type, **extra})
    except Exception as exc:  # noqa: BLE001 - upstream's `.catch` swallows any failure
        logger.debug("Failed to write agent metadata: %s", exc)


def _phase_filtered_pool(
    resolved: Sequence[Tool], get_app_state: Callable[[], AppState]
) -> Callable[[], tuple[Any, ...]]:
    """The worker's `refresh_tools`: the resolved pool under the live phase, rebuilt only on a move."""
    cache: dict[str, Any] = {"phase": None}

    def refresh() -> tuple[Any, ...]:
        ledger = get_app_state().phase_ledger
        if cache["phase"] != ledger.phase:
            cache["phase"] = ledger.phase
            cache["tools"] = assemble_tool_pool(resolved, phase_ledger=ledger)
        tools: tuple[Any, ...] = cache["tools"]
        return tools

    return refresh


async def _subagent_start_context(parent: ToolContext, agent_id: str, agent_type: str) -> list[Message]:
    """SubagentStart hooks' additional context, as one `hook_additional_context` attachment."""
    contexts: list[str] = []
    async for result in execute_subagent_start_hooks(parent, agent_id, agent_type):
        contexts.extend(result.additional_contexts or ())
    if not contexts:
        return []
    message = create_attachment_message(HookAttachment(
        type="hook_additional_context",
        hook_name="SubagentStart",
        tool_use_id=str(uuid.uuid4()),
        hook_event="SubagentStart",
        content=tuple(contexts),
    ))
    return [message] if message is not None else []


def _is_recordable(message: Any) -> bool:
    """`isRecordableMessage`: assistant, user and compact-boundary messages."""
    return isinstance(message, (AIMessage, HumanMessage, ToolMessage)) or is_compact_boundary_message(message)


def run_agent(
    *,
    agent_definition: AgentDefinition,
    prompt_messages: Sequence[Message],
    tool_context: ToolContext,
    config: RunConfig,
    deps: LoopDeps,
    available_tools: Sequence[Tool],
    system_prompt: Sequence[str],
    is_async: bool = False,
    model_override: str | None = None,
    agent_id: str | None = None,
    transcript: Any = None,
    cancel: CancelToken | None = None,
    description: str | None = None,
) -> AsyncIterator[Message]:
    """
    `runAgent`: the worker's messages, as its loop produces them.

    A plain function returning the worker's async generator rather than an async generator
    itself, so `aclose()` on the result always reaches the generator that owns the cleanup —
    including under mutmut, whose trampoline for a top-level async generator re-yields from
    an inner one (`async for i in ...: yield i`) and never forwards `aclose()`.
    """

    worker_id = agent_id or create_agent_id()

    async def worker_run() -> AsyncIterator[Message]:
        parent = tool_context
        agent_id = worker_id
        agent_type = agent_definition.name
        worker_config = replace(config, model=get_agent_model(agent_definition.model, config.model, model_override))

        initial: list[Message] = [*prompt_messages, *await _subagent_start_context(parent, agent_id, agent_type)]
        if agent_definition.skills and parent.skill_registry is not None:
            initial.extend(skill_preload_messages(
                agent_definition.skills, parent.skill_registry, agent_type=agent_type, session_id=parent.run_id))

        get_app_state = _agent_get_app_state(parent.get_app_state, agent_definition.permission_mode, is_async)
        resolved = resolve_agent_tools(agent_definition, available_tools, is_async=is_async).resolved_tools
        refresh_tools = _phase_filtered_pool(resolved, get_app_state)
        # `agentAbortController`: an override, else a fresh one for a background worker (it must
        # outlive a cancelled main thread), else the parent's.
        worker_cancel = cancel if cancel is not None else CancelToken() if is_async else parent.cancel
        worker = replace(
            parent,
            cancel=worker_cancel,
            get_app_state=get_app_state,
            set_app_state=_task_state_only(parent.set_app_state) if is_async else parent.set_app_state,
            local_denial_tracking=DenialTrackingBox() if is_async else parent.local_denial_tracking,
            messages=tuple(initial),
            in_flight_tool_ids=set(),
            tools=refresh_tools(),
            refresh_tools=refresh_tools,
            read_file_state={},
            nested_memory_attachment_triggers=set(),
            loaded_nested_memory_paths=set(),
            # A worker never prompts: its context avoids permission prompts, as upstream's
            # outside swarm mode, so the parent's interactive `canUseTool` is not inherited.
            can_use_tool=None,
            agent_id=agent_id,
            agent_type=agent_type,
            # `createSubagentContext`: the worker's model is its own (`worker_config`).
            main_loop_model=None,
            agent_system_prompt=tuple(system_prompt),
            omit_claude_md=agent_definition.omit_claude_md,
            omit_git_status=agent_type in _NO_GIT_STATUS_AGENT_TYPES,
        )

        turns = {"count": 1}

        def on_event(event: Any) -> None:
            """The worker's own sink: never the parent's (sidechains are not the flow)."""
            # A `Terminated` reason is never `NEXT_TURN`, so only the next-turn transition counts.
            if event.reason != Continue.NEXT_TURN:
                return
            turns["count"] += 1
            limit = agent_definition.max_turns
            if limit is not None and turns["count"] > limit:
                raise _MaxTurnsReached

        worker_deps = replace(deps, emit_event=on_event)
        store = transcript if transcript is not None else parent.transcript_store
        last_uuid = store.record_sidechain_transcript(initial, agent_id) if store is not None else None
        if store is not None:
            _write_agent_metadata(store, agent_id, agent_type, description)

        events = run_loop(initial_loop_state(tuple(initial), worker), worker_config, worker_deps)
        try:
            async for event in events:
                if isinstance(event, Terminated):
                    continue
                if store is not None and _is_recordable(event):
                    last_uuid = store.record_sidechain_transcript([event], agent_id, last_uuid)
                yield event
        except _MaxTurnsReached:
            logger.info("[Agent: %s] Reached max turns limit (%s)", agent_type, agent_definition.max_turns)
        finally:
            # `run_loop` is an async generator; closing it runs its cleanup (history repair).
            close = getattr(events, "aclose", None)
            if close is not None:
                await close()
            worker.read_file_state.clear()
            parent.set_app_state(lambda s: s if agent_id not in s.todos else replace(
                s, todos={k: v for k, v in s.todos.items() if k != agent_id}))

    return worker_run()


def _has_command(name: str, commands: Sequence[SkillCommand]) -> bool:
    return any(name in (c.name, c.user_facing_name()) for c in commands)


def resolve_skill_name(skill_name: str, commands: Sequence[SkillCommand], agent_type: str) -> str | None:
    """`resolveSkillName`: exact, then the agent's plugin prefix, then a `:name` suffix."""
    if _has_command(skill_name, commands):
        return skill_name
    plugin_prefix = agent_type.split(":")[0]
    if plugin_prefix and _has_command(f"{plugin_prefix}:{skill_name}", commands):
        return f"{plugin_prefix}:{skill_name}"
    return next((c.name for c in commands if c.name.endswith(f":{skill_name}")), None)


def skill_preload_messages(
    agent_skills: Sequence[str], registry: SkillRegistry, *, agent_type: str, session_id: str = ""
) -> list[Message]:
    """The preloaded skills' messages, in frontmatter order."""
    commands = registry.get_skill_tool_commands()
    messages: list[Message] = []
    for skill_name in agent_skills:
        resolved = resolve_skill_name(skill_name, commands, agent_type)
        if resolved is None:
            logger.warning("[Agent: %s] Warning: Skill '%s' specified in frontmatter was not found",
                           agent_type, skill_name)
            continue
        skill = next(c for c in commands if resolved in (c.name, c.user_facing_name()))
        logger.debug("[Agent: %s] Preloaded skill '%s'", agent_type, skill_name)
        messages.append(HumanMessage(
            content=[
                {"type": "text", "text": format_skill_loading_metadata(skill_name)},
                {"type": "text", "text": skill.prompt_for_command("", session_id=session_id)},
            ],
            additional_kwargs={"is_meta": True},
        ))
    return messages


__all__ = [
    "create_agent_id",
    "resolve_skill_name",
    "run_agent",
    "skill_preload_messages",
]
