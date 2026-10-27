"""
The production runner (upstream `cli/print.ts`): a development session's run, built once and
driven turn by turn (Phase 11).

`open_session_run` builds a real run — `build_run_config`, the permission context, skills,
MCP, hooks, the transcript, the cost tracker and a fresh `AppStateStore` holding the session's
TDD plan — and keeps it for the whole conversation, as upstream's REPL keeps one conversation
and one `AppState`. `begin_session` submits the first turn and `submit_prompt` every later one,
over the engine's growing history (`ask({prompt, mutableMessages})`). `run_headless` is
`runHeadless` for the development phase: one conversation over the whole plan, with nobody to
answer an 'ask'.

* **Workspace.** Runs are local for now (a decision recorded in
  `docs/refactoring_transition_plan.md`): by default the session's shared
  `LocalWorkspace.for_run(session_id)`, so later sub-requirements build on earlier code. The
  workspace is a parameter, which is the room left for E2B.
* **Ledger and plan.** The run's `AppStateStore` is the only place a ledger is seeded; after it
  `RunTests` is the only writer of observations, and the plan's advance
  (`app/tdd/plan_advance.py`) restarts it for each sub-requirement. The plan is saved beside
  the transcript, and a resumed session keeps its saved plan.
* **Permissions.** Built once at entry the way upstream's startup builds them
  (`build_tool_permission_context`): the mode from the run config or settings
  `defaultMode`, rules from user, project, local, flag and policy settings, with the TDD
  managed tier applied. The context lives in the run's `AppState`, so a worker reading its
  parent's state is held to it. A headless run (`should_avoid_permission_prompts`) denies an
  `ask` that no PermissionRequest hook answers.
* **Transcript.** A `QueryEngine` per run (Phase 6) records it at upstream's location,
  `<config home>/projects/<sanitized workspace>/<run id>.jsonl` — never inside the workspace,
  so agents cannot read or edit their own record and it never reaches the exported artifact.
  Every transition goes to `<run id>/events.jsonl` beside it, with the ledger snapshot and the
  last `RunTests` exit code; the run's cost tracker sits on the tool context so worker API
  calls count toward it.
* **Resume.** The run id is derived from the session's thread id (`session_run_id`), so a
  restarted session finds its transcript and continues that conversation, as
  `claude -p --resume` does: `loadConversationForResume`, SessionStart (`resume`), and an
  interrupted turn re-submitted once (`print.ts`'s auto-resume, always on here); otherwise the
  resume prompt. **The ledger is never restored**: RED has to be observed again.
* **Background work.** One `CommandQueue` per run, the hook registry's, is the context's
  `command_queue`, shared with every worker. When a turn ends while a background task still
  runs, or a main-thread notification is queued, the runner waits and feeds each notification
  back as a new turn of the same conversation (`print.ts`'s `do … while (waitingForAgents)`).
  No ceiling, as upstream: the turn ends when nothing runs and nothing is queued.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
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
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.events import TransitionEventRecorder, events_path
from app.tdd.plan import TddPlan, load_plan, plan_path, save_plan
from app.tool import CancelToken, Tool, tool_context_for
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
#: Fixed namespace for `session_run_id`, so the same thread always names the same run.
RUN_ID_NAMESPACE = uuid.UUID("7d1f9c52-5b0e-4f4e-9a59-2f3c0a8f6b11")


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
    keeps Agent and the task tools out of every worker. `UpdatePlan` (Phase 11) is the main
    thread's alone: the session's plan is changed by the thread the user talks to. The Skill tool, given the run's
    `skill_registry`, joins both rosters, as upstream's base tools hold it. The run's
    `mcp_tools` join the workers' roster here; the main thread's arrive through
    `assemble_tool_pool(mcp_tools=…)`. In coordinator mode the main thread keeps only the
    orchestration tools (`applyCoordinatorToolFilter`).
    """
    from app.tdd.tdd_plan_tool.tdd_plan_tool import build_update_plan_tool
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
    tools: list[Tool] = [
        *built_ins, agent_tool, build_task_output_tool(), build_task_stop_tool(), send_message,
        build_update_plan_tool(),
    ]
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
    *,
    interactive: bool = False,
) -> ToolPermissionContext:
    """
    The run's permission context from its config and settings, anchored at the workspace. An
    `interactive` run can prompt (Phase 11); any other avoids permission prompts.
    """
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
        should_avoid_permission_prompts=not interactive,
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


@dataclass
class DevelopmentRun:
    """
    One development session's open run (Phase 11): its context, store and engine, built once
    and kept for every turn of the conversation, as upstream's REPL keeps one conversation
    and one `AppState` for the whole session.
    """

    run_id: str
    config: RunConfig
    store: AppStateStore
    ctx: Any
    engine: QueryEngine
    transcript: TranscriptStore
    #: Why the last turn's UserPromptSubmit hooks stopped it, when they did.
    stopped_reason: str | None = None
    #: Every event a turn produces, as it arrives (the REPL renders them); `None` discards.
    on_message: Callable[[Any], None] | None = None


def session_run_id(session_id: Any) -> str:
    """The development run of session `session_id`: stable, so a restart finds its transcript."""
    if not session_id:
        return f"loop-{uuid.uuid4().hex}"
    return f"loop-{uuid.uuid5(RUN_ID_NAMESPACE, f'{session_id}:development').hex}"


@asynccontextmanager
async def open_session_run(
    *,
    run_id: str,
    plan: TddPlan,
    workspace: Any,
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
    plan_index: int = 0,
    can_use_tool: Any = None,
) -> AsyncIterator[DevelopmentRun]:
    """
    Build a run once — config, permissions, skills, MCP, hooks, transcript, cost tracker, the
    phase-filtered tool pool and a fresh `AppStateStore` whose ledger starts in RED — yield it
    for as many turns as the caller submits, and tear it down: SessionEnd (`other`), background
    hooks finalized, run memory discarded, the memoized session context reset, MCP closed.

    `postgres_checkpointing` is whether the session shell's checkpointer really persists
    (`Gates.postgres_checkpointing`); `permission_mode`, `settings_path` and
    `dangerously_skip_permissions` are upstream's startup flags, given by the entry point.
    `project_root` is where the project and local settings live (the repository, upstream's
    original cwd); the workspace is the working directory the rules anchor to. `home` and
    `managed_dir` relocate the user and policy scopes (tests use them). With `can_use_tool`
    (`app/hooks/use_can_use_tool.py`) the run is interactive: a permission 'ask' on the main
    thread becomes a question to the user instead of a deny.

    The plan is saved beside the transcript (`app/tdd/plan.py`); when that file already exists
    the session is being resumed and its saved plan wins, so done items stay done. The ledger
    is never restored.
    """
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
    perm = permission_context or run_permission_context(
        config, workspace, project_root, home, managed_dir, interactive=can_use_tool is not None
    )
    store = AppStateStore(state=AppState(tool_permission_context=perm, tdd_plan=plan))
    root_dir = Path(project_root) if project_root is not None else Path.cwd()
    from app.skills.load_skills_dir import get_skill_registry

    # `getSkills`, once per run: bundled, policy, user and the project dirs up to home.
    skill_registry = get_skill_registry(root_dir, home, managed_dir, model=config.model)
    mcp = await connect_mcp_servers(root_dir, run_settings_locations(config, root_dir, home, managed_dir))
    try:
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

        run_dir = getattr(workspace, "run_dir", None)
        transcript_dir = Path(run_dir) if run_dir is not None else None
        cwd = str(getattr(workspace, "root", None) or root_dir)
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
            workspace=workspace,
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
        ctx.can_use_tool = can_use_tool
        saved_plan_path = plan_path(run_id, cwd)
        ctx.tdd_plan_path = str(saved_plan_path)
        saved = load_plan(saved_plan_path)
        if saved is not None:
            store.update(lambda s: replace(s, tdd_plan=saved))
        else:
            save_plan(saved_plan_path, plan)
        recorder = TransitionEventRecorder(store, events_path(run_id, cwd), session_id=run_id, plan_index=plan_index)
        engine = QueryEngine(
            config, replace(deps, emit_event=_both(recorder, deps.emit_event)), transcript=transcript,
            session_id=run_id, cost_tracker=ctx.cost_tracker,
        )
        try:
            yield DevelopmentRun(
                run_id=run_id, config=config, store=store, ctx=ctx, engine=engine, transcript=transcript
            )
        finally:
            from app.context import reset_session_context
            from app.utils.hooks.hooks import execute_session_end_hooks

            await hook_dispatcher.drain_background()
            await execute_session_end_hooks(hook_dispatcher, "other")
            await hook_dispatcher.registry.finalize_pending()
            memory_store.discard_run_memory(run_id)
            # Nothing memoized for this session carries over to the next one.
            reset_session_context(run_id)
            if memory_root is not None:
                shutil.rmtree(memory_root, ignore_errors=True)
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


async def wait_for_background_work(
    engine: QueryEngine, ctx: Any, store: AppStateStore, on_message: Callable[[Any], None] | None = None
) -> None:
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
        async for event in engine.submit(initial_loop_state(messages, turn_ctx)):
            if on_message is not None:
                on_message(event)


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


async def load_initial_messages(
    ctx: Any, transcript: TranscriptStore, sub_req: str, model: str, *, resume_prompt: str | None = None
) -> tuple[Any, str]:
    """
    `print.ts` -> `loadInitialMessages` for one sub-requirement: the history the run starts
    from and the prompt it submits.

    With a transcript on disk the conversation is resumed (`loadConversationForResume`, whose
    SessionStart hooks run with source `resume`); an interrupted turn is taken out and its
    prompt submitted again, else `resume_prompt` (the sub-requirement when there is none) is
    the prompt. Without one the run starts fresh with SessionStart (`startup`).
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
    prompt = resume_prompt if resume_prompt is not None else sub_req
    interruption = resumed.turn_interruption_state
    if interruption.message is not None:
        logger.info("Auto-resuming interrupted turn (kind: %s)", interruption.kind)
        history = remove_interrupted_message(history, interruption.message)
        prompt = _prompt_text(interruption.message)
    session = await process_session_start_hooks(ctx.hook_dispatcher, "resume", model=model)
    session.messages[:0] = history
    return session, prompt


async def _submit(
    run: DevelopmentRun, history: Sequence[Any], prompt_text: str, prefix: Sequence[Any] = ()
) -> Terminal:
    """
    One user turn: UserPromptSubmit, the user-input attachments, the loop, then any background
    work (`print.ts`'s `ask` and its wait). A UserPromptSubmit hook that blocks or says
    `continue: false` stops the turn before the model is called: `hook_stopped`.
    """
    from app.utils.attachments import get_attachment_messages
    from app.utils.session_start import process_user_prompt_submit_hooks

    mode = str(run.store.get().tool_permission_context.mode)
    prompt = await process_user_prompt_submit_hooks(run.ctx, prompt_text, mode)
    if not prompt.should_query:
        # A stopped UserPromptSubmit always carries one of the two messages.
        run.stopped_reason = prompt.blocked_message or prompt.stopped_message
        logger.info("Turn of %s not started: UserPromptSubmit hook stopped it: %s", run.run_id, run.stopped_reason)
        return Terminal.HOOK_STOPPED
    messages: tuple[Any, ...] = (*history, *prefix, HumanMessage(content=prompt_text), *prompt.messages)
    # `processUserInput`: the user-input attachments (the TDD state among them), persisted into
    # the history the loop starts from.
    messages = (*messages, *await get_attachment_messages(run.ctx, messages, user_input=prompt_text))
    if run.ctx.cancel.cancelled:
        # A fresh abort signal per turn, as upstream creates an `AbortController` per query: the
        # Esc that ended the last turn must not end this one.
        run.ctx.cancel = CancelToken()
    turn_ctx = replace(run.ctx, messages=messages)
    try:
        async for event in run.engine.submit(initial_loop_state(messages, turn_ctx)):
            if run.on_message is not None:
                run.on_message(event)
        await wait_for_background_work(run.engine, run.ctx, run.store, run.on_message)
        reason = run.engine.terminal.reason if run.engine.terminal is not None else Terminal.MODEL_ERROR
    except Exception:
        logger.exception("Loop %s crashed", run.run_id)
        reason = Terminal.MODEL_ERROR
    if run.engine.result is not None:
        logger.info("Loop %s result: %s", run.run_id, run.engine.result)
    return reason


async def begin_session(run: DevelopmentRun, fresh_prompt: str, resume_prompt: str | None = None) -> Terminal:
    """
    The first turn: SessionStart and `fresh_prompt`, or — with a transcript on disk — the
    resumed conversation, continued with the interrupted prompt or else `resume_prompt`.
    """
    session, prompt_text = await load_initial_messages(
        run.ctx, run.transcript, fresh_prompt, run.config.model, resume_prompt=resume_prompt
    )
    first = [HumanMessage(content=session.initial_user_message)] if session.initial_user_message else []
    return await _submit(run, session.messages, prompt_text, first)


async def submit_prompt(run: DevelopmentRun, prompt_text: str) -> Terminal:
    """A later turn of the same conversation: the user's prompt after everything said so far."""
    return await _submit(run, run.engine.mutable_messages, prompt_text)


@dataclass(frozen=True, slots=True)
class DevelopmentOutcome:
    """How a development session ended: the last turn's terminal (`None` if none ran) and the plan."""

    terminal: Terminal | None
    plan: TddPlan

    @property
    def completed(self) -> bool:
        """Every sub-requirement was observed Red -> Green and the loop ended `completed`."""
        return self.terminal == Terminal.COMPLETED and self.plan.is_complete


async def run_headless(
    session_id: str, specification: str, plan_texts: Sequence[str], *, workspace: Any, **run_options: Any
) -> DevelopmentOutcome:
    """
    `runHeadless` for the development phase (Phase 11): one conversation over the whole plan
    with nobody to ask, so a permission 'ask' (a plan change among them) is denied and the
    session ends at the first turn that ends without the plan done (user decision 2026-10-27).
    """
    from app.session.prompts import development_resume_prompt, development_start_prompt

    async with open_session_run(run_id=session_run_id(session_id), plan=TddPlan.from_texts(list(plan_texts)),
                                workspace=workspace, **run_options) as run:
        plan = run.store.get().tdd_plan
        reason = await begin_session(
            run, development_start_prompt(specification, plan), development_resume_prompt(plan)
        )
        return DevelopmentOutcome(terminal=reason, plan=run.store.get().tdd_plan)


__all__ = [
    "DevelopmentOutcome",
    "DevelopmentRun",
    "begin_session",
    "build_builtin_tools",
    "coordinator_context",
    "load_initial_messages",
    "main_thread_tools",
    "next_main_thread_command",
    "open_session_run",
    "run_headless",
    "run_agent_memory_store",
    "run_permission_context",
    "run_settings_locations",
    "session_run_id",
    "session_workspace",
    "submit_prompt",
    "wait_for_background_work",
]
