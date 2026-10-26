"""
The ambient context threaded through the loop and into every tool call.

This is the third of the three kinds in §3.1 of `docs/transition_elaboration_plan.md`, and
the only mutable one. `RunConfig` is frozen at entry and `LoopState` is replaced wholesale
at each continue site; `ToolContext` is the thing that is *re-spread* — copied with one or
two members changed — as an iteration proceeds. Upstream writes that as an object spread
(`{...toolUseContext, messages}`); here it is `dataclasses.replace`, which is the same
operation with a name.

Ported from `reference/claude-code/src/Tool.ts` -> `ToolUseContext`, which carries roughly
fifty members. This is the minimum surface named in Part B3 — abort, app state, messages,
in-flight ids — and it is deliberately the whole of it. The tool-facing members (workspace,
permission mode, hook dispatcher, result counters) arrive with the tool layer that needs
them, so that each one enters against a call site rather than against a guess.

Two members are callables rather than data, and that is load-bearing. Upstream injects
`getAppState`/`setAppState` because an async subagent is handed a *no-op* setter: its
writes must not reach the parent's store. Making them parameters rather than a reference to
a module-level store is what lets Part I3 assemble an isolated worker without a conditional
anywhere in the tool path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.loop.context.app_state import AppState, AppStateStore
from app.loop.context.file_state import FileState
from app.loop.messages import Message
from app.loop.permissions.denial_tracking import DenialTrackingState


@dataclass(slots=True)
class CancelToken:
    """
    Cooperative cancellation, checked between units of work.

    Deliberately not a thread-kill. A tool already in flight against the sandbox has paid
    for its round trip, and abandoning it mid-write would leave the workspace in a state no
    record describes. Part F3 builds the three-controller abort tree — turn, siblings,
    single tool — out of separate instances of this, which is why cancellation is a token
    passed by reference rather than a flag on the state record.
    """

    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


@dataclass(slots=True)
class ToolContext:
    """
    What the loop threads through a tool call.

    Mutable by design — it is the one member of the three-way split that is. `messages` is
    an immutable snapshot rather than a live list, so a tool cannot append to the
    conversation behind the loop's back; the loop hands down a new context when the
    conversation moves on.

    `in_flight_tool_ids` is the exception and is mutable: tools start and finish inside a
    single iteration, and the set is the record of which ones are outstanding right now.
    Part B5 decides how concurrent updates to it are coordinated, when there is a scheduler
    to coordinate them.

    `read_file_state` is upstream's `readFileState`: what `ReadFile` saw of each file, which
    `Edit` and `WriteFile` check before writing. A worker receives a copy, never the
    parent's dict (`createSubagentContext` clones it the same way).

    `refresh_tools` is upstream's `options.refreshTools`: the loop calls it between turns
    and swaps `tools` only when the callback returns a *different object*. The identity
    check is the point — an unchanged roster keeps the same tuple, so the tool block of the
    prompt stays byte-identical and the prompt cache is not broken for nothing. For TDD it
    is what makes a RED to GREEN ledger move change the tools the next turn is offered.
    """

    cancel: CancelToken
    get_app_state: Callable[[], AppState]
    set_app_state: Callable[[Callable[[AppState], AppState]], None]
    messages: tuple[Message, ...] = ()
    in_flight_tool_ids: set[str] = field(default_factory=set)
    tools: tuple[Any, ...] = ()
    permission_context: Any = None
    workspace: Any = None
    hook_dispatcher: Any = None
    read_file_state: dict[str, FileState] = field(default_factory=dict)
    refresh_tools: Callable[[], tuple[Any, ...]] | None = None
    #: The run this context belongs to. Oversized tool results are filed under it, the way
    #: upstream files them under the session id.
    run_id: str = ""
    #: Which agent this context belongs to, `None` for the main thread. Upstream's
    #: `agentId`: per-agent state (the todo list today, more in Part J) is keyed by it, so a
    #: worker's bookkeeping cannot overwrite the coordinator's.
    agent_id: str | None = None
    #: The worker's agent type (upstream `agentType`), `None` on the main thread. Hooks read
    #: it: `SubagentStop` matches on it and every hook input carries it.
    agent_type: str | None = None
    #: Upstream `localDenialTracking`: a worker whose app-state writes are discarded keeps its
    #: own denial tally here (a mutable box, mutated in place as upstream mutates its object).
    local_denial_tracking: DenialTrackingBox | None = None
    #: The auto-mode classifier to call (`classify_yolo_action`'s signature); `None` means the
    #: production classifier. A seam for tests, which must never reach a live model.
    auto_mode_classifier: Callable[..., Any] | None = None
    #: Upstream `contentReplacementState`: which tool results the per-message budget has
    #: already seen, and the preview each replaced one became. Mutable and carried across
    #: turns, so a decision once made is replayed byte-identically (prompt-cache stability).
    #: `None` means the budget is off — upstream provisions it only behind a GrowthBook flag
    #: that is off in the external build (`provision_content_replacement_state`).
    content_replacement_state: ContentReplacementState | None = None
    #: The run's agent-memory store (`agents/memory.py`), owned by the runner: rooted outside
    #: the workspace, and its run-scoped memory discarded when the run ends (Part I5).
    agent_memory_store: Any = None
    #: The run's `CostTracker` (`app/loop/cost/`), upstream's session-global cost state made
    #: per run. Fed as a callback on every model call; a worker shares its parent's.
    cost_tracker: Any = None
    #: The session transcript's path (upstream's `getTranscriptPath()`, session-global): what
    #: a compaction's summary points at. A worker shares its parent's.
    transcript_path: str | None = None
    #: The session's `TranscriptStore` (`app/loop/storage/`), upstream's session-global
    #: `Project`: a worker records its sidechain through it (`recordSidechainTranscript`).
    transcript_store: Any = None
    #: The run's `CommandQueue` (`messageQueueManager.ts`): task and hook notifications, shared
    #: by reference with every worker, as upstream's process-global queue is.
    command_queue: Any = None
    #: The run's `SkillRegistry` (`app/loop/skills/`), upstream's per-session commands: loaded
    #: once by the runner and shared by reference with every worker.
    skill_registry: Any = None
    #: Upstream `options.mcpClients`: the run's MCP connections (`app/loop/mcp/`), read by the
    #: `mcp_instructions` prompt section, its delta attachment and the resource tools.
    mcp_clients: tuple[Any, ...] = ()
    #: Upstream `options.mainLoopModel` set mid-run: a skill's `model:` override, applied by
    #: the Skill tool's context modifier. `None` is the run's `RunConfig.model`.
    main_loop_model: str | None = None
    #: Upstream `toolUseContext.toolUseId`: the id of the call being executed, set only on the
    #: copy the pipeline hands `tool.call` (a background agent's notification carries it).
    tool_use_id: str | None = None
    #: The system prompt this context's model calls send instead of the default sections
    #: (`runAgent.ts`: `query({ systemPrompt: agentSystemPrompt })`). `None` is the main
    #: thread's `getSystemPrompt`; the system context is appended either way.
    agent_system_prompt: tuple[str, ...] | None = None
    #: `runAgent.ts` -> `shouldOmitClaudeMd`: the user context goes without `claudeMd`.
    omit_claude_md: bool = False
    #: `runAgent.ts` -> `systemContextNoGit` (Explore/Plan): the system context goes without
    #: `gitStatus`.
    omit_git_status: bool = False
    #: Keys merged into the user context after the session's own, as upstream's
    #: `getCoordinatorUserContext` adds `workerToolsContext` on the coordinator.
    extra_user_context: Mapping[str, str] | None = None
    #: Upstream `nestedMemoryAttachmentTriggers`: paths `ReadFile` read this turn, whose
    #: nested `TDDAGENTS.md` / conditional rules the `nested_memory` attachment loads next.
    nested_memory_attachment_triggers: set[str] = field(default_factory=set)
    #: Upstream `loadedNestedMemoryPaths`: memory files already attached, never sent twice.
    loaded_nested_memory_paths: set[str] = field(default_factory=set)


@dataclass
class ContentReplacementState:
    """`ContentReplacementState`: `seenIds` and `replacements` (tool_use_id -> preview)."""

    seen_ids: set[str] = field(default_factory=set)
    replacements: dict[str, str] = field(default_factory=dict)


@dataclass
class DenialTrackingBox:
    """A worker's local denial tally (`localDenialTracking`), mutated in place."""

    state: DenialTrackingState = field(default_factory=lambda: DenialTrackingState())


def tool_context_for(
    store: AppStateStore,
    *,
    messages: tuple[Message, ...] = (),
    tools: tuple[Any, ...] = (),
    permission_context: Any = None,
    workspace: Any = None,
    hook_dispatcher: Any = None,
    refresh_tools: Callable[[], tuple[Any, ...]] | None = None,
    run_id: str = "",
    agent_id: str | None = None,
    agent_type: str | None = None,
    agent_memory_store: Any = None,
    cost_tracker: Any = None,
    transcript_path: str | None = None,
    transcript_store: Any = None,
    command_queue: Any = None,
    skill_registry: Any = None,
    mcp_clients: tuple[Any, ...] = (),
    main_loop_model: str | None = None,
    agent_system_prompt: tuple[str, ...] | None = None,
    omit_claude_md: bool = False,
    omit_git_status: bool = False,
    extra_user_context: Mapping[str, str] | None = None,
) -> ToolContext:
    """
    Build a context wired to a store, which is what the main loop wants.

    A worker that must not write the parent's state is built by hand instead, with
    `discard_app_state_update` as its setter; that asymmetry is the isolation boundary and
    is meant to be visible at the call site.
    """
    return ToolContext(
        cancel=CancelToken(),
        get_app_state=store.get,
        set_app_state=store.update,
        messages=messages,
        tools=tools,
        permission_context=permission_context,
        workspace=workspace,
        hook_dispatcher=hook_dispatcher,
        refresh_tools=refresh_tools,
        run_id=run_id,
        agent_id=agent_id,
        agent_type=agent_type,
        agent_memory_store=agent_memory_store,
        cost_tracker=cost_tracker,
        transcript_path=transcript_path,
        transcript_store=transcript_store,
        command_queue=command_queue,
        skill_registry=skill_registry,
        mcp_clients=mcp_clients,
        main_loop_model=main_loop_model,
        agent_system_prompt=agent_system_prompt,
        omit_claude_md=omit_claude_md,
        omit_git_status=omit_git_status,
        extra_user_context=extra_user_context,
    )
