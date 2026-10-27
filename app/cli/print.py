"""
The production runner: one sub-requirement through one `run_loop` (Phase 0 baseline).

`tdd_loop_runner` is what the LangGraph session shell calls per plan item. It builds a
real `LoopState` with `initial_loop_state` and `build_run_config`, gives the tools a real
workspace, and reports only what the ledger observed.

* **Workspace.** Runs are local for now (a decision recorded in
  `docs/refactoring_transition_plan.md`): by default the session's shared
  `LocalWorkspace.for_run(session_id)`, so later sub-requirements build on earlier code. The
  workspace is a parameter, which is the room left for E2B.
* **Ledger.** Each sub-requirement gets a fresh `AppStateStore`; that construction is the
  only place a ledger is seeded, and after it `RunTests` is the only writer.
* **Permissions.** Built once at entry the way upstream's startup builds them
  (`build_tool_permission_context`): the mode from the run config or settings
  `defaultMode`, rules from user, project, local, flag and policy settings, with the TDD
  managed tier applied. The context lives in the sub-requirement's `AppState`, so a worker
  reading its parent's state is held to it. The run is headless
  (`should_avoid_permission_prompts`): an `ask` that no PermissionRequest hook answers is a deny.
* **Transcript.** A `QueryEngine` per sub-requirement (Phase 6) records it at upstream's
  location, `<config home>/projects/<sanitized workspace>/<run id>.jsonl` — never inside the
  workspace, so agents cannot read or edit their own record and it never reaches the
  exported artifact. Every transition goes to `<run id>/events.jsonl` beside it, with the
  ledger snapshot and the last `RunTests` exit code; the run's cost tracker sits on the
  tool context so worker API calls count toward it.
* **Resume.** A sub-requirement's run id is derived from the session's thread id and the
  plan index (`item_run_id`), so when the session shell re-runs an item a previous process
  left unfinished, its transcript is already on disk and the run continues that
  conversation, as `claude -p --resume` does: `loadConversationForResume`, SessionStart
  (`resume`), and an interrupted turn re-submitted once (`print.ts`'s auto-resume, which
  upstream gates on `CLAUDE_CODE_RESUME_INTERRUPTED_TURN`; the shell's restart is exactly the
  gateway restart that variable exists for, so here it is always on). Otherwise the
  sub-requirement is the new prompt. **The ledger is never restored**: a resumed run starts
  from a fresh ledger, so RED has to be observed again.
* **Background work.** One `CommandQueue` per run, the hook registry's, is the context's
  `command_queue`, shared with every worker. When a turn ends while a background task still
  runs, or a main-thread notification is queued, the runner waits and feeds each notification
  back as a new turn of the same conversation (`print.ts`'s `do … while (waitingForAgents)`).
  No ceiling, as upstream: the loop ends when nothing runs and nothing is queued.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.coordinator.coordinator_mode import (
    apply_coordinator_tool_filter,
    get_coordinator_system_prompt,
    get_coordinator_user_context,
    is_coordinator_mode,
)
from app.cost_tracker import CostTracker
from app.query.config import RunConfig, build_run_config
from app.query.deps import LoopDeps
from app.query.state import initial_loop_state
from app.query.transitions import Terminal
from app.query_engine import QueryEngine
from app.session.state import PlanItemResult, SessionState
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.events import TransitionEventRecorder, events_path
from app.tool import Tool, tool_context_for
from app.tools.agent_tool.agent_memory import AgentMemoryStore
from app.tools.tools import assemble_tool_pool
from app.types.permissions import ToolPermissionContext
from app.utils.hooks.hooks import HookDispatcher
from app.utils.message_queue_manager import CommandQueue, QueuedCommand
from app.utils.session_storage import TranscriptStore
from app.utils.settings.settings import (
    SettingsLocations,
    build_tool_permission_context,
)
from app.utils.task.framework import get_running_tasks, is_background_task
from app.workspace.local import LocalWorkspace

logger = logging.getLogger("loop_runner")

#: `print.ts`: the modes a headless run accepts from the queue.
HEADLESS_COMMAND_MODES = ("prompt", "orphaned-permission", "task-notification")
#: `print.ts`'s `sleep(100)` while background tasks run and nothing is queued.
WAITING_FOR_AGENTS_POLL_SECONDS = 0.1
#: Fixed namespace for `item_run_id`, so the same (thread, index) always names the same run.
RUN_ID_NAMESPACE = uuid.UUID("7d1f9c52-5b0e-4f4e-9a59-2f3c0a8f6b11")


def item_run_id(session_id: Any, idx: int) -> str:
    """
    The run id of plan item `idx` of session `session_id`: stable across processes, so a
    re-run of an unfinished item finds its transcript. Without a session id nothing can be
    resumed, and the id is fresh.
    """
    if not session_id:
        return f"loop-{uuid.uuid4().hex}"
    return f"loop-{uuid.uuid5(RUN_ID_NAMESPACE, f'{session_id}:{idx}').hex}"


def build_builtin_tools() -> list[Tool]:
    """
    The Phase 0 roster: the file primitives, Bash and RunTests.

    No per-run template vars, as in claude-code: a tool's prompt is static, and what varies per
    run (the workspace, the phase) reaches the tool through `ToolContext` at call time.
    """
    from app.tdd.run_tests_tool.run_tests_tool import build_run_tests_tool
    from app.tools.bash_tool.bash_tool import build_bash_tool
    from app.tools.file_edit_tool.file_edit_tool import build_edit_tool
    from app.tools.file_read_tool.file_read_tool import build_read_file_tool
    from app.tools.file_write_tool.file_write_tool import build_write_file_tool
    from app.tools.glob_tool.glob_tool import build_glob_tool
    from app.tools.grep_tool.grep_tool import build_grep_tool
    from app.tools.todo_write_tool.todo_write_tool import build_todo_write_tool

    return [
        build_read_file_tool(),
        build_write_file_tool(),
        build_edit_tool(),
        build_glob_tool(),
        build_grep_tool(),
        build_bash_tool(),
        build_run_tests_tool(),
        build_todo_write_tool(),
    ]


def main_thread_tools(
    config: RunConfig,
    deps: LoopDeps,
    project_root: Path,
    home: Path | None,
    *,
    skill_registry: Any = None,
    mcp_tools: Sequence[Tool] = (),
) -> list[Tool]:
    """
    The main thread's roster: the built-ins plus the Agent tool, as upstream's main thread
    always has it, and the task tools (`getAllBaseTools`: TaskOutput, TaskStop, and
    SendMessage, which disables itself unless agent teams are on). Workers draw from the
    built-ins alone (upstream's `assembleToolPool` for the worker), and `resolveAgentTools`
    keeps Agent and the task tools out of every worker. The Skill tool, given the run's
    `skill_registry`, joins both rosters, as upstream's base tools hold it. The run's
    `mcp_tools` join the workers' roster here; the main thread's arrive through
    `assemble_tool_pool(mcp_tools=…)`. In coordinator mode the main thread keeps only the
    orchestration tools (`applyCoordinatorToolFilter`).
    """
    from app.tools.agent_tool.agent_tool import build_agent_tool
    from app.tools.agent_tool.load_agents_dir import (
        get_agent_definitions_with_overrides,
    )
    from app.tools.send_message_tool.send_message_tool import build_send_message_tool
    from app.tools.task_output_tool.task_output_tool import build_task_output_tool
    from app.tools.task_stop_tool.task_stop_tool import build_task_stop_tool

    built_ins = build_builtin_tools()
    coordinator = is_coordinator_mode(config)
    definitions = get_agent_definitions_with_overrides(project_dir=project_root, user_home=home)
    if skill_registry is not None:
        from app.tools.skill_tool.skill_tool import build_skill_tool

        built_ins.append(build_skill_tool(skill_registry, config=config, deps=deps, agent_definitions=definitions))
    worker_tools = [*built_ins, *mcp_tools]
    agent_tool = build_agent_tool(
        definitions=definitions, worker_tools=worker_tools, config=config, deps=deps, is_coordinator=coordinator
    )
    send_message = build_send_message_tool(
        definitions=definitions, worker_tools=worker_tools, config=config, deps=deps
    )
    tools: list[Tool] = [*built_ins, agent_tool, build_task_output_tool(), build_task_stop_tool(), send_message]
    return apply_coordinator_tool_filter(tools) if coordinator else tools


def coordinator_context(config: RunConfig) -> dict[str, Any]:
    """The coordinator's system prompt and user context; nothing outside coordinator mode."""
    if not is_coordinator_mode(config):
        return {}
    return {
        "agent_system_prompt": (get_coordinator_system_prompt(),),
        "extra_user_context": get_coordinator_user_context(),
    }


def session_workspace(thread_id: str) -> LocalWorkspace:
    """
    The session's shared local workspace, with its own Python environment
    (`app/workspace/pyenv.py`) first on `PATH` for every command the tools run.
    """
    from app.workspace.pyenv import ensure_session_python

    probe = LocalWorkspace.for_run(thread_id)
    python = ensure_session_python(probe.run_dir)
    return LocalWorkspace.for_run(thread_id, env=python.env())


def run_permission_context(
    config: RunConfig,
    workspace: Any,
    project_root: Path | None = None,
    home: Path | None = None,
    managed_dir: Path | str | None = None,
) -> ToolPermissionContext:
    """The run's permission context from its config and settings, anchored at the workspace."""
    from app.utils.settings.settings import MANAGED_DIR

    root = getattr(workspace, "root", None)
    return build_tool_permission_context(
        project_root if project_root is not None else Path.cwd(),
        home,
        flag_settings_path=config.settings_path,
        managed_dir=managed_dir if managed_dir is not None else MANAGED_DIR,
        permission_mode_cli=config.permission_mode,
        dangerously_skip_permissions=config.dangerously_skip_permissions,
        working_dir=Path(root) if root is not None else None,
    )


def run_settings_locations(
    config: RunConfig,
    project_root: Path,
    home: Path | None = None,
    managed_dir: Path | str | None = None,
) -> SettingsLocations:
    """Where this run's settings live: the same files its permission context is built from."""
    from app.utils.settings.settings import MANAGED_DIR

    return SettingsLocations(
        project_root=project_root,
        home=home,
        flag_settings_path=Path(config.settings_path) if config.settings_path is not None else None,
        managed_dir=Path(managed_dir if managed_dir is not None else MANAGED_DIR),
    )


def run_agent_memory_store(run_dir: Path | None) -> tuple[AgentMemoryStore, Path | None]:
    """
    The run's agent-memory store (Part I5), rooted **outside** the workspace: under the
    session's run directory when the workspace has one, else in a temporary directory the
    runner removes afterwards (returned as the second element). Agents can therefore never
    read another run's run-scoped memory through the workspace.
    """
    if run_dir is not None:
        return AgentMemoryStore(base_dir=run_dir), None
    root = Path(tempfile.mkdtemp(prefix="tddagents-agent-memory-"))
    return AgentMemoryStore(base_dir=root), root


def default_workspace(state: SessionState) -> LocalWorkspace:
    """The session's shared local workspace; a session without an id is a wiring bug."""
    session_id = state.get("session_id")
    if not session_id:
        raise ValueError("tdd_loop_runner needs a session_id to locate the session workspace")
    return session_workspace(str(session_id))


async def tdd_loop_runner(
    sub_req: str,
    idx: int,
    state: SessionState,
    *,
    workspace: Any = None,
    deps: LoopDeps | None = None,
    permission_context: ToolPermissionContext | None = None,
    project_root: Path | None = None,
    home: Path | None = None,
    managed_dir: Path | str | None = None,
    hook_dispatcher: HookDispatcher | None = None,
    postgres_checkpointing: bool = False,
    permission_mode: str | None = None,
    settings_path: str | None = None,
    dangerously_skip_permissions: bool = False,
) -> PlanItemResult:
    """
    Run one sub-requirement through the loop and report what the ledger observed.

    `postgres_checkpointing` is whether the session shell's checkpointer really persists
    (`Gates.postgres_checkpointing`); `permission_mode`, `settings_path` and
    `dangerously_skip_permissions` are upstream's startup flags, given by the entry point.

    `project_root` is where the project and local settings live (the repository, upstream's
    original cwd); the workspace is the working directory the rules anchor to.
    `home` and `managed_dir` relocate the user and policy scopes (tests use them).

    Hooks follow upstream's headless session: SessionStart (`startup`) and UserPromptSubmit
    (the sub-requirement) before the first model call, SessionEnd (`other`) after the last,
    then any background hook still running is killed. A UserPromptSubmit hook that blocks or
    says `continue: false` stops the run before the model is called: `hook_stopped`, failed.
    """
    run_id = item_run_id(state.get("session_id"), idx)
    ws = workspace if workspace is not None else default_workspace(state)
    if deps is None:
        from app.query.deps import get_production_deps

        deps = get_production_deps()
    config = build_run_config(
        run_id,
        postgres_checkpointing=postgres_checkpointing,
        permission_mode=permission_mode,
        settings_path=settings_path,
        dangerously_skip_permissions=dangerously_skip_permissions,
    )
    perm = permission_context or run_permission_context(config, ws, project_root, home, managed_dir)

    store = AppStateStore(state=AppState(tool_permission_context=perm))
    root_dir = Path(project_root) if project_root is not None else Path.cwd()
    from app.skills.load_skills_dir import get_skill_registry

    # `getSkills`, once per run: bundled, policy, user and the project dirs up to home.
    skill_registry = get_skill_registry(root_dir, home, managed_dir, model=config.model)
    mcp = await connect_mcp_servers(root_dir, run_settings_locations(config, root_dir, home, managed_dir))
    try:
        return await _run_connected(
            sub_req, idx, run_id=run_id, ws=ws, deps=deps, config=config, store=store, root_dir=root_dir,
            home=home, managed_dir=managed_dir, hook_dispatcher=hook_dispatcher, skill_registry=skill_registry,
            mcp=mcp,
        )
    finally:
        await mcp.aclose()


async def connect_mcp_servers(root_dir: Path, locations: SettingsLocations) -> Any:
    """
    `getClaudeCodeMcpConfigs` + `getMcpToolsCommandsAndResources` for the run: every configured
    server connected (a server that fails is recorded, never fatal), headless as `claude -p`,
    so project servers load without an approval prompt.
    """
    from app.services.mcp.config import get_claude_code_mcp_configs
    from app.services.mcp.mcp_connection_manager import McpConnectionManager

    configs, errors = get_claude_code_mcp_configs(root_dir, locations)
    for error in errors:
        logger.warning("MCP config error: %s", error)
    manager = McpConnectionManager(cwd=str(root_dir))
    await manager.connect(configs)
    return manager


async def _run_connected(
    sub_req: str,
    idx: int,
    *,
    run_id: str,
    ws: Any,
    deps: LoopDeps,
    config: RunConfig,
    store: AppStateStore,
    root_dir: Path,
    home: Path | None,
    managed_dir: Path | str | None,
    hook_dispatcher: HookDispatcher | None,
    skill_registry: Any,
    mcp: Any,
) -> PlanItemResult:
    """The run once its MCP servers are connected; the caller closes them."""
    ledger = store.get().phase_ledger
    built_ins = main_thread_tools(
        config, deps, root_dir, home, skill_registry=skill_registry, mcp_tools=mcp.tools
    )
    # `mergeAndFilterTools`: the coordinator filter runs over the merged pool, MCP included.
    main_mcp = apply_coordinator_tool_filter(mcp.tools) if is_coordinator_mode(config) else list(mcp.tools)
    pool_cache: dict[str, Any] = {
        "phase": ledger.phase,
        "tools": assemble_tool_pool(built_ins, main_mcp, phase_ledger=ledger),
    }

    def refresh_tools() -> tuple[Tool, ...]:
        """
        `refreshTools` for this loop: re-derive the phase-filtered pool when, and only
        when, the phase has moved.

        The cached tuple is returned unchanged otherwise, because the engine compares by
        identity — a fresh but equal tuple would rebuild the serialized tool block every
        turn and break the prompt cache for no reason.
        """
        current = store.get().phase_ledger
        if current.phase != pool_cache["phase"]:
            pool_cache["phase"] = current.phase
            pool_cache["tools"] = assemble_tool_pool(built_ins, main_mcp, phase_ledger=current)
        tools_now: tuple[Tool, ...] = pool_cache["tools"]
        return tools_now

    run_dir = getattr(ws, "run_dir", None)
    transcript_dir = Path(run_dir) if run_dir is not None else None
    cwd = str(getattr(ws, "root", None) or root_dir)
    transcript = TranscriptStore(run_id, cwd)

    if hook_dispatcher is None:
        from app.utils.hooks.hook_dispatcher import build_hook_dispatcher

        hook_dispatcher = build_hook_dispatcher(
            run_settings_locations(config, root_dir, home, managed_dir),
            session_id=run_id,
            cwd=cwd,
            project_dir=str(root_dir),
            model=config.model,
            transcript_path=str(transcript.path),
        )

    memory_store, memory_root = run_agent_memory_store(transcript_dir)
    ctx = tool_context_for(
        store,
        messages=(),
        tools=refresh_tools(),
        workspace=ws,
        hook_dispatcher=hook_dispatcher,
        refresh_tools=refresh_tools,
        run_id=run_id,
        agent_memory_store=memory_store,
        cost_tracker=CostTracker(config.model),
        transcript_path=str(transcript.path),
        transcript_store=transcript,
        command_queue=hook_dispatcher.registry.queue,
        skill_registry=skill_registry,
        mcp_clients=tuple(mcp.clients),
        **coordinator_context(config),
    )
    try:
        return await _run_with_hooks(
            sub_req, idx, run_id, ctx, store, config, deps, transcript, events_path(run_id, cwd)
        )
    finally:
        from app.context import reset_session_context
        from app.utils.hooks.hooks import execute_session_end_hooks

        await hook_dispatcher.drain_background()
        await execute_session_end_hooks(hook_dispatcher, "other")
        await hook_dispatcher.registry.finalize_pending()
        memory_store.discard_run_memory(run_id)
        # The next sub-requirement is a new session: nothing memoized for this one carries over.
        reset_session_context(run_id)
        if memory_root is not None:
            shutil.rmtree(memory_root, ignore_errors=True)


def _is_main_thread(command: QueuedCommand) -> bool:
    """`isMainThread`: worker-addressed commands are drained by the worker's own loop."""
    return command.agent_id is None


def next_main_thread_command(queue: CommandQueue) -> QueuedCommand | None:
    """
    `drainCommandQueue`'s `dequeue(isMainThread)`, refusing a mode a headless run cannot run.

    Upstream batches consecutive `prompt` commands into one turn; nothing here enqueues a
    main-thread prompt, so each command is its own turn.
    """
    command = queue.dequeue(_is_main_thread)
    if command is not None and command.mode not in HEADLESS_COMMAND_MODES:
        raise ValueError("only prompt commands are supported in streaming mode")
    return command


def _has_running_background_task(store: AppStateStore) -> bool:
    """`getRunningTasks(state).some(isBackgroundTask)`; this build has no teammates to exclude."""
    return any(is_background_task(task) for task in get_running_tasks(store.get()))


async def wait_for_background_work(engine: QueryEngine, ctx: Any, store: AppStateStore) -> None:
    """
    `print.ts`'s `do … while (waitingForAgents)`: after a turn, while a background task runs or
    a main-thread command is queued, wait for the queue and submit each command as a new turn
    of the same conversation (`ask({ prompt, mutableMessages })`).

    A task notification is not a typed prompt, so no UserPromptSubmit hook runs for it
    (`processUserInput` runs them for `prompt` mode only); its user-input attachments are
    collected as for any input.
    """
    from app.utils.attachments import get_attachment_messages

    queue: CommandQueue = ctx.command_queue
    while True:
        command = next_main_thread_command(queue)
        if command is None:
            if not _has_running_background_task(store):
                return
            await asyncio.sleep(WAITING_FOR_AGENTS_POLL_SECONDS)
            continue
        messages: tuple[Any, ...] = (*engine.mutable_messages, HumanMessage(content=command.value))
        messages = (*messages, *await get_attachment_messages(ctx, messages, user_input=command.value))
        turn_ctx = replace(ctx, messages=messages)
        logger.info("Delivering queued %s as a new turn", command.mode)
        async for _ in engine.submit(initial_loop_state(messages, turn_ctx)):
            pass


def _both(first: Any, second: Any) -> Any:
    """The run's event recorder, then whatever sink the caller's deps already had."""

    def emit(event: Any) -> None:
        first(event)
        second(event)

    return emit


def _prompt_text(message: Any) -> str:
    """A user message's text, as `print.ts` re-enqueues it: a string, or its text blocks joined."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text"
    )


async def load_initial_messages(ctx: Any, transcript: TranscriptStore, sub_req: str, model: str) -> tuple[Any, str]:
    """
    `print.ts` -> `loadInitialMessages` for one sub-requirement: the history the run starts
    from and the prompt it submits.

    With a transcript on disk the conversation is resumed (`loadConversationForResume`, whose
    SessionStart hooks run with source `resume`); an interrupted turn is taken out and its
    prompt submitted again, else the sub-requirement is the prompt. Without one the run
    starts fresh with SessionStart (`startup`).
    """
    from app.utils.conversation_recovery import (
        load_conversation_for_resume,
        remove_interrupted_message,
    )
    from app.utils.session_start import process_session_start_hooks

    resumed = load_conversation_for_resume(transcript.path)
    if resumed is None or not resumed.messages:
        return await process_session_start_hooks(ctx.hook_dispatcher, "startup", model=model), sub_req
    history: list[Any] = list(resumed.messages)
    prompt = sub_req
    interruption = resumed.turn_interruption_state
    if interruption.message is not None:
        logger.info("Auto-resuming interrupted turn (kind: %s)", interruption.kind)
        history = remove_interrupted_message(history, interruption.message)
        prompt = _prompt_text(interruption.message)
    session = await process_session_start_hooks(ctx.hook_dispatcher, "resume", model=model)
    session.messages[:0] = history
    return session, prompt


async def _run_with_hooks(
    sub_req: str,
    idx: int,
    run_id: str,
    ctx: Any,
    store: AppStateStore,
    config: RunConfig,
    deps: LoopDeps,
    transcript: TranscriptStore,
    events_file: Path,
) -> PlanItemResult:
    """SessionStart (or the resumed conversation) and UserPromptSubmit, then the loop itself."""
    from app.utils.session_start import process_user_prompt_submit_hooks

    session, prompt_text = await load_initial_messages(ctx, transcript, sub_req, config.model)
    mode = str(store.get().tool_permission_context.mode)
    prompt = await process_user_prompt_submit_hooks(ctx, prompt_text, mode)
    if not prompt.should_query:
        # A stopped UserPromptSubmit always carries one of the two messages.
        reason = prompt.blocked_message or prompt.stopped_message
        logger.info("Loop %s not started: UserPromptSubmit hook stopped it: %s", run_id, reason)
        final = store.get().phase_ledger
        return PlanItemResult(
            index=idx,
            sub_requirement=sub_req,
            status="failed",
            terminal_reason=str(Terminal.HOOK_STOPPED),
            red_confirmed=final.red_confirmed,
            green_passed=final.green_passed,
            error_message=reason,
        )
    first_turn = [HumanMessage(content=session.initial_user_message)] if session.initial_user_message else []
    messages = (*session.messages, *first_turn, HumanMessage(content=prompt_text), *prompt.messages)
    # `processUserInput`: the user-input attachments (the initial TDD state among them),
    # persisted into the history the loop starts from.
    from app.utils.attachments import get_attachment_messages

    attachments = await get_attachment_messages(ctx, messages, user_input=prompt_text)
    messages = (*messages, *attachments)
    ctx = replace(ctx, messages=messages)
    loop_state = initial_loop_state(messages, ctx)

    logger.info("Launching TDD loop %s for sub-requirement %d: %s", run_id, idx, sub_req)
    recorder = TransitionEventRecorder(store, events_file, session_id=run_id, plan_index=idx)
    engine = QueryEngine(
        config, replace(deps, emit_event=_both(recorder, deps.emit_event)), transcript=transcript, session_id=run_id,
        cost_tracker=ctx.cost_tracker,
    )
    try:
        async for _ in engine.submit(loop_state):
            pass
        await wait_for_background_work(engine, ctx, store)
        reason = engine.terminal.reason if engine.terminal is not None else Terminal.MODEL_ERROR
    except Exception:
        logger.exception("Loop %s crashed", run_id)
        reason = Terminal.MODEL_ERROR
    if engine.result is not None:
        logger.info("Loop %s result: %s", run_id, engine.result)

    final = store.get().phase_ledger
    succeeded = reason == Terminal.COMPLETED
    logger.info(
        "Loop %s finished: reason=%s red=%s green=%s", run_id, reason, final.red_confirmed, final.green_passed
    )
    return PlanItemResult(
        index=idx,
        sub_requirement=sub_req,
        status="success" if succeeded else "failed",
        terminal_reason=str(reason),
        red_confirmed=final.red_confirmed,
        green_passed=final.green_passed,
        error_message=None if succeeded else f"Terminated early: {reason}",
    )


__all__ = [
    "build_builtin_tools",
    "coordinator_context",
    "default_workspace",
    "item_run_id",
    "load_initial_messages",
    "main_thread_tools",
    "next_main_thread_command",
    "run_agent_memory_store",
    "run_permission_context",
    "run_settings_locations",
    "session_workspace",
    "tdd_loop_runner",
    "wait_for_background_work",
]
