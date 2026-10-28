"""
Tool protocol and build_tool factory with fail-closed defaults (Part B2).

Ported from:
- `reference/claude-code/src/Tool.ts` -> `Tool`, `toolMatchesName`, `findToolByName`
- `app/tools/base.py` (Phase 1B) -> fail-closed defaults and protocol adaptation

Core dataclasses for the tool layer (Part B1).

Ported from:
- `reference/claude-code/src/Tool.ts` -> `ValidationResult`
- `reference/claude-code/src/utils/permissions/PermissionResult.ts` -> `PermissionResult`
- `reference/claude-code/src/types/tools.ts` -> Tool results and execution outcomes

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

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import (
    Any,
    Protocol,
    runtime_checkable,
)

from langchain_core.messages import ToolMessage

from app.state.app_state_store import AppState, AppStateStore
from app.types.permissions import PermissionResult
from app.utils.file_state_cache import FileState
from app.utils.messages import Message
from app.utils.permissions.denial_tracking import DenialTrackingState
from app.utils.tool_result_storage import MAX_TOOL_RESULT_BYTES


def default_map_result(result: ToolResult, tool_use_id: str) -> ToolMessage:
    """
    Default mapping from a ToolResult to a LangChain ToolMessage.
    """
    status = "error" if result.is_error else "success"
    additional_kwargs: dict[str, Any] = {}
    if result.hook_stopped_continuation:
        additional_kwargs["hook_stopped_continuation"] = True
    if result.system_reminder:
        additional_kwargs["system_reminder"] = result.system_reminder
    if result.is_error:
        additional_kwargs["is_error"] = True
    if result.exit_code is not None:
        additional_kwargs["exit_code"] = result.exit_code

    return ToolMessage(
        content=list(result.content_blocks) if result.content_blocks else result.content,
        tool_call_id=tool_use_id,
        status=status,
        additional_kwargs=additional_kwargs,
    )


@runtime_checkable
class Tool(Protocol):
    """
    Capability surface exposed by a loop tool.
    """

    @property
    def name(self) -> str: ...

    @property
    def prompt(self) -> str: ...

    @property
    def input_schema(self) -> dict[str, Any]: ...

    @property
    def aliases(self) -> tuple[str, ...]: ...

    @property
    def is_mcp(self) -> bool: ...

    def description(self, input: dict[str, Any]) -> str: ...
    def is_enabled(self) -> bool: ...
    def is_concurrency_safe(self, input: dict[str, Any]) -> bool: ...
    def is_read_only(self, input: dict[str, Any]) -> bool: ...
    def is_destructive(self, input: dict[str, Any]) -> bool: ...
    def is_implementation_writer(self) -> bool: ...
    def is_test_writer(self) -> bool: ...
    def validate_input(self, input: dict[str, Any], context: ToolContext) -> ValidationResult: ...

    def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> Awaitable[PermissionResult] | PermissionResult: ...
    def call(self, input: dict[str, Any], context: ToolContext) -> Awaitable[ToolResult]: ...
    def map_result(self, result: ToolResult, tool_use_id: str) -> ToolMessage: ...


@dataclass(frozen=True, slots=True)
class BuiltTool:
    """
    Concrete tool assembled by `build_tool` with fail-closed exception guards.
    """

    name: str
    prompt: str
    _call: Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult] | ToolResult]
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    aliases: tuple[str, ...] = ()
    is_mcp: bool = False
    #: `Tool.ts` -> `maxResultSizeChars`. `math.inf` is the hard opt-out from persistence,
    #: which upstream uses for `Read` alone: persisting a read creates a
    #: ReadFile -> file -> ReadFile loop and the tool already bounds itself. Upstream makes
    #: this field required on every tool; here the fallback is the global byte budget, so a
    #: tool that forgets it is governed rather than unbounded.
    max_result_size_chars: float = MAX_TOOL_RESULT_BYTES
    _description: Callable[[dict[str, Any]], str] = field(default_factory=lambda: (lambda args: ""))
    _is_enabled: Callable[[], bool] = field(default_factory=lambda: (lambda: True))
    _is_concurrency_safe: Callable[[dict[str, Any]], bool] = field(default_factory=lambda: (lambda args: False))
    _is_read_only: Callable[[dict[str, Any]], bool] = field(default_factory=lambda: (lambda args: False))
    _is_destructive: Callable[[dict[str, Any]], bool] = field(default_factory=lambda: (lambda args: False))
    _is_implementation_writer: Callable[[], bool] = field(default_factory=lambda: (lambda: False))
    _is_test_writer: Callable[[], bool] = field(default_factory=lambda: (lambda: False))
    _requires_user_interaction: Callable[[dict[str, Any]], bool] | Callable[[], bool] = field(
        default_factory=lambda: (lambda args: False)
    )
    _validate_input: Callable[[dict[str, Any], ToolContext], ValidationResult] = field(
        default_factory=lambda: (lambda args, ctx: ValidationResult(valid=True))
    )
    _check_permissions: Callable[[dict[str, Any], ToolContext], Awaitable[PermissionResult] | PermissionResult] = (
        field(default_factory=lambda: (lambda args, ctx: PermissionResult(behavior="allow")))
    )
    _map_result: Callable[[ToolResult, str], ToolMessage] = default_map_result
    #: `Tool.ts` -> `preparePermissionMatcher`: given a validated input, a predicate over a
    #: permission-rule content string (`git *`, `src/**`). Hook `if` conditions use it;
    #: `None` means the tool matches only a bare `Tool` condition, as upstream.
    _prepare_permission_matcher: Callable[[dict[str, Any]], Callable[[str], bool]] | None = None

    def description(self, input: dict[str, Any]) -> str:
        return self._description(input)

    def is_enabled(self) -> bool:
        try:
            return bool(self._is_enabled())
        except Exception:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return False

    def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
        """
        Fail-closed predicate: defaults to False and any exception is caught and treated as False.
        """
        try:
            return bool(self._is_concurrency_safe(input))
        except Exception:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return False

    def is_read_only(self, input: dict[str, Any]) -> bool:
        """
        Fail-closed predicate: defaults to False and any exception is caught and treated as False.
        """
        try:
            return bool(self._is_read_only(input))
        except Exception:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return False

    def is_destructive(self, input: dict[str, Any]) -> bool:
        try:
            return bool(self._is_destructive(input))
        except Exception:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return False

    def is_implementation_writer(self) -> bool:
        try:
            return bool(self._is_implementation_writer())
        except Exception:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return False

    def is_test_writer(self) -> bool:
        try:
            return bool(self._is_test_writer())
        except Exception:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return False

    def requires_user_interaction(self, input: dict[str, Any] | None = None) -> bool:
        fn = self._requires_user_interaction
        try:
            if input is not None:
                try:
                    return bool(fn(input))  # type: ignore[call-arg]
                except TypeError:
                    return bool(fn())  # type: ignore[call-arg]
            return bool(fn())  # type: ignore[call-arg]
        except Exception:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return False

    def validate_input(self, input: dict[str, Any], context: ToolContext) -> ValidationResult:
        try:
            return self._validate_input(input, context)
        except Exception as exc:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return ValidationResult(valid=False, message=str(exc))

    async def check_permissions(self, input: dict[str, Any], context: ToolContext) -> PermissionResult:
        res = self._check_permissions(input, context)
        if inspect.isawaitable(res):
            return await res
        return res

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        res = self._call(input, context)
        if inspect.isawaitable(res):
            return await res
        return res

    def map_result(self, result: ToolResult, tool_use_id: str) -> ToolMessage:
        return self._map_result(result, tool_use_id)

    def prepare_permission_matcher(self, input: dict[str, Any]) -> Callable[[str], bool] | None:
        """The tool's rule-content matcher for `input`, or `None` when it has none."""
        if self._prepare_permission_matcher is None:
            return None
        return self._prepare_permission_matcher(input)


def build_tool(
    *,
    name: str,
    prompt: str,
    call: Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult] | ToolResult],
    input_schema: dict[str, Any] | None = None,
    description: Callable[[dict[str, Any]], str] | None = None,
    is_enabled: Callable[[], bool] | None = None,
    is_concurrency_safe: Callable[[dict[str, Any]], bool] | None = None,
    is_read_only: Callable[[dict[str, Any]], bool] | None = None,
    is_destructive: Callable[[dict[str, Any]], bool] | None = None,
    is_implementation_writer: Callable[[], bool] | bool = False,
    is_test_writer: Callable[[], bool] | bool = False,
    requires_user_interaction: (
        Callable[[dict[str, Any]], bool] | Callable[[], bool] | None
    ) = None,
    validate_input: Callable[[dict[str, Any], ToolContext], ValidationResult] | None = None,
    check_permissions: Callable[[dict[str, Any], ToolContext], Awaitable[PermissionResult] | PermissionResult]
    | None = None,
    map_result: Callable[[ToolResult, str], ToolMessage] | None = None,
    aliases: Sequence[str] = (),
    is_mcp: bool = False,
    max_result_size_chars: float = MAX_TOOL_RESULT_BYTES,
    prepare_permission_matcher: Callable[[dict[str, Any]], Callable[[str], bool]] | None = None,
) -> BuiltTool:
    """
    Builds a Tool with fail-closed defaults for omitted members.
    """
    if isinstance(is_implementation_writer, bool):
        impl_fn: Callable[[], bool] = (lambda: is_implementation_writer)
    else:
        impl_fn = is_implementation_writer

    if isinstance(is_test_writer, bool):
        test_fn: Callable[[], bool] = (lambda: is_test_writer)
    else:
        test_fn = is_test_writer

    return BuiltTool(
        name=name,
        prompt=prompt,
        _call=call,
        input_schema=input_schema if input_schema is not None else {"type": "object"},
        aliases=tuple(aliases),
        is_mcp=is_mcp,
        max_result_size_chars=max_result_size_chars,
        _description=description if description is not None else (lambda args: name),
        _is_enabled=is_enabled if is_enabled is not None else (lambda: True),
        _is_concurrency_safe=is_concurrency_safe if is_concurrency_safe is not None else (lambda args: False),
        _is_read_only=is_read_only if is_read_only is not None else (lambda args: False),
        _is_destructive=is_destructive if is_destructive is not None else (lambda args: False),
        _is_implementation_writer=impl_fn,
        _is_test_writer=test_fn,

        _requires_user_interaction=(
            requires_user_interaction if requires_user_interaction is not None else (lambda args: False)
        ),
        _validate_input=(
            validate_input if validate_input is not None else (lambda args, ctx: ValidationResult(valid=True))
        ),
        _check_permissions=(
            check_permissions
            if check_permissions is not None
            else (lambda args, ctx: PermissionResult(behavior="allow"))
        ),
        _map_result=map_result if map_result is not None else default_map_result,
        _prepare_permission_matcher=prepare_permission_matcher,
    )


def tool_matches_name(tool: Tool, name: str) -> bool:
    """
    Check if a tool matches a given name (primary name or aliases).
    """
    return tool.name == name or name in getattr(tool, "aliases", ())


def find_tool_by_name(tools: Sequence[Tool], name: str) -> Tool | None:
    """
    Find a tool by name in a sequence of tools. Primary names match first; aliases match second.
    """
    for tool in tools:
        if tool.name == name:
            return tool
    for tool in tools:
        if name in getattr(tool, "aliases", ()):
            return tool
    return None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """
    Outcome of validating tool arguments before permission checks and execution.
    """

    valid: bool
    message: str = ""
    error_code: int = 0


@dataclass(frozen=True, slots=True)
class ToolResult:
    """
    Standard result returned by a tool call.
    """

    content: str
    is_error: bool = False
    tool_use_id: str = ""
    system_reminder: str | None = None
    hook_stopped_continuation: bool = False
    context_modifier: Callable[[ToolContext], ToolContext] | None = None
    exit_code: int | None = None
    metadata: dict[str, Any] | None = None
    #: Upstream `ToolResult.newMessages`: messages the call adds to the conversation after its
    #: result (the Skill tool's skill body). Emitted after PostToolUse's attachments and before
    #: a preventing hook's `hook_stopped_continuation` (`toolExecution.ts`).
    new_messages: tuple[Any, ...] = ()
    #: The result's content as blocks (LangChain standard `text` / `image` blocks), where
    #: upstream's `mapToolResultToToolResultBlockParam` returns a block list (ReadFile's images
    #: and notebooks). `content` stays the text form hooks and governance read.
    content_blocks: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ContextModifier:
    """
    Context modification queued during concurrent or serial tool execution.
    """

    tool_use_id: str
    modify_context: Callable[[ToolContext], ToolContext]


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
    #: The run's agent-memory store (`tools/agent_tool/agent_memory.py`), owned by the runner: rooted outside
    #: the workspace, and its run-scoped memory discarded when the run ends (Part I5).
    agent_memory_store: Any = None
    #: The run's `CostTracker` (`app/cost_tracker.py`), upstream's session-global cost state made
    #: per run. Fed as a callback on every model call; a worker shares its parent's.
    cost_tracker: Any = None
    #: The session transcript's path (upstream's `getTranscriptPath()`, session-global): what
    #: a compaction's summary points at. A worker shares its parent's.
    transcript_path: str | None = None
    #: The session's `TranscriptStore` (`app/utils/`), upstream's session-global
    #: `Project`: a worker records its sidechain through it (`recordSidechainTranscript`).
    transcript_store: Any = None
    #: The run's `CommandQueue` (`messageQueueManager.ts`): task and hook notifications, shared
    #: by reference with every worker, as upstream's process-global queue is.
    command_queue: Any = None
    #: The run's `SkillRegistry` (`app/skills/`), upstream's per-session commands: loaded
    #: once by the runner and shared by reference with every worker.
    skill_registry: Any = None
    #: Upstream `options.mcpClients`: the run's MCP connections (`app/services/mcp/`), read by the
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
    #: Upstream `criticalSystemReminder_EXPERIMENTAL`: the agent definition's short reminder,
    #: re-sent as a `critical_system_reminder` attachment at every turn of the worker.
    critical_system_reminder: str | None = None
    #: Upstream `shareSetAppState`: `False` for a background worker, whose app-state writes
    #: are discarded (only its task state reaches the root store). RunTests reads it, so a
    #: background run does not claim to move the TDD ledger.
    shares_app_state: bool = True
    #: Keys merged into the user context after the session's own, as upstream's
    #: `getCoordinatorUserContext` adds `workerToolsContext` on the coordinator.
    extra_user_context: Mapping[str, str] | None = None
    #: Upstream `nestedMemoryAttachmentTriggers`: paths `ReadFile` read this turn, whose
    #: nested `TDDAGENTS.md` / conditional rules the `nested_memory` attachment loads next.
    nested_memory_attachment_triggers: set[str] = field(default_factory=set)
    #: Upstream `loadedNestedMemoryPaths`: memory files already attached, never sent twice.
    loaded_nested_memory_paths: set[str] = field(default_factory=set)
    #: Where the session's TDD plan is saved (`app/tdd/plan.py`, Phase 11); `None` keeps it in
    #: memory only. A worker shares its parent's.
    tdd_plan_path: str | None = None
    #: Upstream `canUseTool` for an interactive session (`app/hooks/use_can_use_tool.py`, Phase
    #: 11): what a permission 'ask' becomes. `None` is a headless run, where the gate already
    #: turned every 'ask' into a deny. Never handed to a worker.
    can_use_tool: Callable[..., Any] | None = None


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


__all__ = [
    "ContextModifier",
    "PermissionResult",
    "Tool",
    "ToolResult",
    "ValidationResult",
    "build_tool",
    "default_map_result",
    "find_tool_by_name",
    "tool_matches_name",
]
