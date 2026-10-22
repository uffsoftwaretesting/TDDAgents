"""
One worker's run (Phase 7): the port of `runAgent`.

Ported from `reference/claude-code/src/tools/AgentTool/runAgent.ts` -> `runAgent`, with the
context construction of `utils/forkedAgent.ts` -> `createSubagentContext`. A worker is a
second `run_loop` over a fresh message list (its prompt, plus any SubagentStart context),
with its own system prompt, its own resolved tool pool and its own identity. What it shares
with its parent is decided exactly as upstream decides it:

* **App state.** A synchronous worker writes through its parent's `set_app_state`
  (`shareSetAppState: !isAsync`). That is what lets a tester's `RunTests` confirm RED on the
  coordinator's ledger — the ledger *is* app state. An asynchronous worker's writes are
  discarded, and it keeps its own denial tally.
* **Permission mode.** The definition's `permissionMode` applies unless the parent runs in
  `bypassPermissions`, `acceptEdits` or `auto`; an asynchronous worker never prompts.
* **Abort.** A synchronous worker shares its parent's cancel token.

Two TDDAgents additions keep the TDD barriers inside the worker: the resolved pool is
filtered by the live phase ledger (barrier 2, `assemble_tool_pool`), re-derived between
turns when the phase moves; and the worker's transitions never reach the parent's event
sink — sidechains are excluded from the sub-requirement's flow (Part L). The worker's
`Stop` hooks fire as `SubagentStop`, because its context carries an `agent_id`.

`maxTurns` is upstream's own optional ceiling and applies only when a definition declares it:
the worker ends before the turn past the limit is sent, where upstream yields its
`max_turns_reached` attachment and stops.

Not ported: MCP servers per agent, preloaded skills, frontmatter hooks (no session-hook
store here), effort, the fork path's exact-tools prefix, Perfetto tracing, agent metadata
files, and the shell/monitor task kill (no background tasks yet).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import replace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.agents.definition import AgentDefinition
from app.loop.agents.resolution import resolve_agent_tools
from app.loop.compact.compact import is_compact_boundary_message
from app.loop.config import RunConfig
from app.loop.context import (
    AppState,
    DenialTrackingBox,
    ToolContext,
    discard_app_state_update,
)
from app.loop.deps import LoopDeps
from app.loop.engine import run_loop
from app.loop.hooks.execute import execute_subagent_start_hooks
from app.loop.hooks.messages import HookAttachment, create_attachment_message
from app.loop.messages import Message
from app.loop.model_aliases import get_agent_model
from app.loop.permissions.types import PermissionMode
from app.loop.state import initial_loop_state
from app.loop.tools.base import Tool
from app.loop.tools.pool import assemble_tool_pool
from app.loop.transitions import Continue, Terminated

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


def _phase_filtered_pool(
    resolved: Sequence[Tool], get_app_state: Callable[[], AppState]
) -> Callable[[], tuple[Any, ...]]:
    """The worker's `refresh_tools`: the resolved pool under the live phase, rebuilt only on a move."""
    cache: dict[str, Any] = {"phase": None, "tools": ()}

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


async def run_agent(
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
) -> AsyncIterator[Message]:
    """`runAgent`: yield the worker's messages as its loop produces them."""
    parent = tool_context
    agent_id = agent_id or create_agent_id()
    agent_type = agent_definition.name
    worker_config = replace(config, model=get_agent_model(agent_definition.model, config.model, model_override))

    initial: list[Message] = [*prompt_messages, *await _subagent_start_context(parent, agent_id, agent_type)]

    get_app_state = _agent_get_app_state(parent.get_app_state, agent_definition.permission_mode, is_async)
    resolved = resolve_agent_tools(agent_definition, available_tools, is_async=is_async).resolved_tools
    refresh_tools = _phase_filtered_pool(resolved, get_app_state)
    worker = replace(
        parent,
        get_app_state=get_app_state,
        set_app_state=discard_app_state_update if is_async else parent.set_app_state,
        local_denial_tracking=DenialTrackingBox() if is_async else parent.local_denial_tracking,
        messages=tuple(initial),
        in_flight_tool_ids=set(),
        tools=refresh_tools(),
        refresh_tools=refresh_tools,
        read_file_state={},
        nested_memory_attachment_triggers=set(),
        loaded_nested_memory_paths=set(),
        agent_id=agent_id,
        agent_type=agent_type,
        agent_system_prompt=tuple(system_prompt),
        omit_claude_md=agent_definition.omit_claude_md,
        omit_git_status=agent_type in _NO_GIT_STATUS_AGENT_TYPES,
    )

    turns = {"count": 1}

    def on_event(event: Any) -> None:
        """The worker's own sink: never the parent's (sidechains are not the flow)."""
        if isinstance(event, Terminated) or getattr(event, "reason", None) != Continue.NEXT_TURN:
            return
        turns["count"] += 1
        limit = agent_definition.max_turns
        if limit is not None and turns["count"] > limit:
            raise _MaxTurnsReached

    worker_deps = replace(deps, emit_event=on_event)
    store = transcript if transcript is not None else parent.transcript_store
    last_uuid = store.record_sidechain_transcript(initial, agent_id) if store is not None else None

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


__all__ = ["create_agent_id", "run_agent"]
