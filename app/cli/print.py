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
import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.bootstrap.state import SessionBootstrap
from app.coordinator.coordinator_mode import (
    apply_coordinator_tool_filter,
    get_coordinator_system_prompt,
    get_coordinator_user_context,
    is_coordinator_mode,
)
from app.cost_tracker import CostTracker
from app.query.config import RunConfig, build_run_config
from app.query.deps import LoopDeps
from app.query.transitions import Terminal
from app.query_engine import QueryEngine, ask
from app.state.app_state_store import AppState, AppStateStore
from app.tasks.local_shell_task.kill_shell_tasks import kill_all_shell_tasks
from app.tdd.events import TransitionEventRecorder, events_path
from app.tdd.plan import TddPlan, load_plan, plan_path, save_plan
from app.tdd.suite_fingerprint import fingerprint_test_files
from app.tool import Tool, tool_context_for
from app.tools.agent_tool.agent_memory import AgentMemoryStore
from app.tools.tools import assemble_tool_pool
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.claudemd import is_env_truthy
from app.utils.conversation_recovery import ConversationForResume
from app.utils.file_history import (
    copy_file_history_for_resume,
    handle_rewind_files,
    update_file_history_state_for,
)
from app.utils.hooks.hooks import HookDispatcher
from app.utils.message_queue_manager import CommandQueue, QueuedCommand
from app.utils.permissions.permission_setup import set_permission_mode
from app.utils.plans import load_session_plans
from app.utils.session_storage import TranscriptStore
from app.utils.settings.settings import (
    SettingsLocations,
    build_tool_permission_context,
    load_settings,
)
from app.utils.task.framework import get_running_tasks, is_background_task
from app.utils.tool_result_storage import config_home
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
    The Phase 0 roster: the file primitives, Bash and RunTests, TodoWrite, and WebFetch (2026-10-28,
    upstream's base tool, which its verification agent relies on).

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
    from app.tools.web_fetch_tool.web_fetch_tool import build_web_fetch_tool

    return [
        build_read_file_tool(),
        build_write_file_tool(),
        build_edit_tool(),
        build_glob_tool(),
        build_grep_tool(),
        build_bash_tool(),
        build_run_tests_tool(),
        build_todo_write_tool(),
        build_web_fetch_tool(),
    ]


def main_thread_tools(
    config: RunConfig,
    deps: LoopDeps,
    project_root: Path,
    home: Path | None,
    *,
    skill_registry: Any = None,
    mcp_tools: Sequence[Tool] = (),
    coordinator_filter: bool = True,
) -> list[Tool]:
    """
    The main thread's roster: the built-ins plus the Agent tool, as upstream's main thread
    always has it, and the task tools (`getAllBaseTools`: TaskOutput, TaskStop, and
    SendMessage, which disables itself unless agent teams are on). Workers draw from the
    built-ins alone (upstream's `assembleToolPool` for the worker), and `resolveAgentTools`
    keeps Agent and the task tools out of every worker. `UpdatePlan` (Phase 11) is the main
    thread's alone: the session's plan is changed by the thread the user talks to. So is
    `AskUserQuestion`, which upstream's `ALL_AGENT_DISALLOWED_TOOLS` keeps from every worker.
    The Skill tool, given the run's
    `skill_registry`, joins both rosters, as upstream's base tools hold it. The run's
    `mcp_tools` join the workers' roster here; the main thread's arrive through
    `assemble_tool_pool(mcp_tools=…)`. `EnterPlanMode` and `ExitPlanMode` are the main thread's
    (upstream's `ALL_AGENT_DISALLOWED_TOOLS`). In coordinator mode the main thread keeps only the
    orchestration tools (`applyCoordinatorToolFilter`) unless `coordinator_filter` is off: the
    planning phase runs with the whole roster, since the coordinator's filter would leave the
    Analyst neither the read-only tools nor ExitPlanMode.
    """
    from app.tdd.tdd_plan_tool.tdd_plan_tool import build_update_plan_tool
    from app.tools.agent_tool.agent_tool import build_agent_tool
    from app.tools.agent_tool.load_agents_dir import (
        get_agent_definitions_with_overrides,
    )
    from app.tools.ask_user_question_tool.ask_user_question_tool import (
        build_ask_user_question_tool,
    )
    from app.tools.enter_plan_mode_tool.enter_plan_mode_tool import (
        build_enter_plan_mode_tool,
    )
    from app.tools.exit_plan_mode_tool.exit_plan_mode_tool import (
        build_exit_plan_mode_tool,
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
        build_update_plan_tool(), build_ask_user_question_tool(), build_enter_plan_mode_tool(),
        build_exit_plan_mode_tool(),
    ]
    return apply_coordinator_tool_filter(tools) if coordinator and coordinator_filter else tools


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
    The session's project (Phase 13): its own folder outside TDDAgents', with seeded settings
    (`app/workspace/project.py`) and its own venv (`app/workspace/pyenv.py`), activated for every
    command by the session environment script (`app/utils/session_environment.py`).
    """
    from app.workspace.project import prepare_project
    from app.workspace.pyenv import ensure_session_python

    workspace = LocalWorkspace.for_run(thread_id)
    project = prepare_project(workspace.root)
    python = ensure_session_python(project)
    return LocalWorkspace.for_run(thread_id, env=python.write_env_file(workspace.run_dir / "session-env.sh"))


def project_root_of(workspace: Any, project_root: Path | str | None = None) -> Path:
    """
    The project folder settings, skills, agents, MCP servers and hooks are read from: upstream's
    `getOriginalCwd()`, the folder claude-code was opened in. Since Phase 13 that is the session's
    project (the workspace root), never TDDAgents' own folder; a workspace with no root (a test
    double) falls back to the process cwd.
    """
    if project_root is not None:
        return Path(project_root)
    root = getattr(workspace, "root", None)
    return Path(root) if root is not None else Path.cwd()


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
        project_root_of(workspace, project_root),
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
    #: The last turn's engine (`ask` builds one per prompt, as `print.ts` does).
    engine: QueryEngine
    transcript: TranscriptStore
    #: The loop's dependencies, the run's event recorder included.
    deps: Any = None
    #: Upstream `mutableMessages`: the conversation every `ask` continues and mutates.
    messages: list[Any] = field(default_factory=list)
    #: Upstream `activeUserSpecifiedModel`: the model `set_model` chose for the next turns.
    user_specified_model: str | None = None
    #: Why the last turn's UserPromptSubmit hooks stopped it, when they did.
    stopped_reason: str | None = None
    #: Every event a turn produces, as it arrives (the REPL renders them); `None` discards.
    on_message: Callable[[Any], None] | None = None
    #: The run's `events.jsonl` sink (`TransitionEventRecorder`).
    recorder: Any = None


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
    permission_mode: str | None = None,
    settings_path: str | None = None,
    dangerously_skip_permissions: bool = False,
    plan_index: int = 0,
    can_use_tool: Any = None,
    planning: bool = False,
    custom_system_prompt: str | None = None,
    append_system_prompt: str | None = None,
) -> AsyncIterator[DevelopmentRun]:
    """
    Build a run once — config, permissions, skills, MCP, hooks, transcript, cost tracker, the
    phase-filtered tool pool and a fresh `AppStateStore` whose ledger starts in RED — yield it
    for as many turns as the caller submits, and tear it down: SessionEnd (`other`), background
    hooks finalized, run memory discarded, the memoized session context reset, MCP closed.

    `permission_mode`, `settings_path` and
    `dangerously_skip_permissions` are upstream's startup flags, given by the entry point.
    `project_root` overrides where the project settings, skills, agents, MCP servers and hooks
    are read from; by default that is the workspace root, the session's project (Phase 13,
    upstream's original cwd), and the workspace is the working directory the rules anchor to. `home` and
    `managed_dir` relocate the user and policy scopes (tests use them). With `can_use_tool`
    (`app/hooks/use_can_use_tool.py`) the run is interactive: a permission 'ask' on the main
    thread becomes a question to the user instead of a deny.

    The plan is saved beside the transcript (`app/tdd/plan.py`); when that file already exists
    the session is being resumed and its saved plan wins, so done items stay done. The ledger
    is never restored. With `planning`, a session whose plan has no sub-requirements opens in
    plan mode (the Analyst, `app/utils/permissions/permission_setup.py`), its configured mode
    kept as `prePlanMode` for `ExitPlanMode` to restore; its plan file is `ctx.session_plans`.
    """
    if deps is None:
        from app.query.deps import get_production_deps

        deps = get_production_deps()
    config = build_run_config(
        run_id,
        permission_mode=permission_mode,
        settings_path=settings_path,
        dangerously_skip_permissions=dangerously_skip_permissions,
    )
    perm = permission_context or run_permission_context(
        config, workspace, project_root, home, managed_dir, interactive=can_use_tool is not None
    )
    root_dir = project_root_of(workspace, project_root)
    cwd = str(getattr(workspace, "root", None) or root_dir)
    # Upstream's `STATE` for this run: its session id, original directory and interactivity.
    bootstrap = SessionBootstrap(session_id=run_id, original_cwd=cwd, is_interactive=can_use_tool is not None)
    store = AppStateStore(state=AppState(tool_permission_context=perm, tdd_plan=plan, bootstrap=bootstrap))
    from app.skills.load_skills_dir import get_skill_registry

    # `getSkills`, once per run: bundled, policy, user and the project dirs up to home.
    skill_registry = get_skill_registry(root_dir, home, managed_dir, model=config.model)
    mcp = await connect_mcp_servers(root_dir, run_settings_locations(config, root_dir, home, managed_dir))
    try:
        coordinator = is_coordinator_mode(config)
        built_ins = main_thread_tools(
            config, deps, root_dir, home, skill_registry=skill_registry, mcp_tools=mcp.tools
        )
        planning_tools = (
            main_thread_tools(config, deps, root_dir, home, skill_registry=skill_registry, mcp_tools=mcp.tools,
                              coordinator_filter=False)
            if coordinator else built_ins
        )
        # `mergeAndFilterTools`: the coordinator filter runs over the merged pool, MCP included.
        main_mcp = apply_coordinator_tool_filter(mcp.tools) if coordinator else list(mcp.tools)
        pool_cache: dict[str, Any] = {"key": None, "tools": ()}

        def refresh_tools() -> tuple[Tool, ...]:
            """
            `refreshTools` for this loop: re-derive the phase-filtered pool when, and only
            when, the phase, plan mode or the deny rules (`assembleToolPool`'s
            `filterToolsByDenyRules`) have changed.

            The cached tuple is returned unchanged otherwise, because the engine compares by
            identity — a fresh but equal tuple would rebuild the serialized tool block every
            turn and break the prompt cache for no reason.
            """
            state = store.get()
            # The phase rules guard the current sub-requirement only (user decision 2026-10-31).
            current = state.phase_ledger if state.tdd_plan.current is not None else None
            permission_context = state.tool_permission_context
            in_plan = permission_context.mode == PermissionMode.PLAN
            key = (current.phase if current is not None else None, in_plan, permission_context.always_deny_rules)
            if key != pool_cache["key"]:
                pool_cache["key"] = key
                tools, extra = (planning_tools, list(mcp.tools)) if in_plan else (built_ins, main_mcp)
                pool_cache["tools"] = assemble_tool_pool(
                    tools, extra, phase_ledger=current, permission_context=permission_context)
            tools_now: tuple[Tool, ...] = pool_cache["tools"]
            return tools_now

        run_dir = getattr(workspace, "run_dir", None)
        transcript_dir = Path(run_dir) if run_dir is not None else None
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
        ctx.custom_system_prompt = custom_system_prompt
        ctx.append_system_prompt = append_system_prompt
        # `updateFileHistoryState` over the run's store (`QueryEngine.ts` / `REPL.tsx`).
        ctx.update_file_history_state = update_file_history_state_for(store.update)
        saved_plan_path = plan_path(run_id, cwd)
        ctx.tdd_plan_path = str(saved_plan_path)
        ctx.session_plans = load_session_plans(
            saved_plan_path.parent,
            load_settings(run_settings_locations(config, root_dir, home, managed_dir)),
            root_dir,
            config_home(),
        )
        saved = load_plan(saved_plan_path)
        if saved is not None:
            store.update(lambda s: replace(s, tdd_plan=saved))
        else:
            save_plan(saved_plan_path, plan)
        if planning and not store.get().tdd_plan.items:
            store.update(lambda s: set_permission_mode(s, PermissionMode.PLAN))
            ctx.tools = refresh_tools()
        # The current sub-requirement starts here (or restarts, on resume: the ledger is never
        # restored), so its tests are fingerprinted as RED's baseline (Phase 12).
        baseline = fingerprint_test_files(workspace)
        store.update(lambda s: replace(s, tdd_item_test_baseline=baseline))
        recorder = TransitionEventRecorder(store, events_path(run_id, cwd), session_id=run_id, plan_index=plan_index)
        run_deps = replace(deps, emit_event=_both(recorder, deps.emit_event))
        engine = QueryEngine(config, run_deps, transcript=transcript, session_id=run_id,
                             cost_tracker=ctx.cost_tracker, context=ctx)
        run = DevelopmentRun(run_id=run_id, config=config, store=store, ctx=ctx, engine=engine,
                             transcript=transcript, deps=run_deps, messages=engine.mutable_messages,
                             recorder=recorder)
        unsubscribe = ctx.command_queue.subscribe(lambda: interrupt_on_now_priority(run.engine, ctx.command_queue))
        try:
            yield run
        finally:
            from app.context import reset_session_context
            from app.utils.hooks.hooks import execute_session_end_hooks

            unsubscribe()
            # Upstream's `registerCleanup` per shell task: nothing the session started outlives it.
            kill_all_shell_tasks(store.get, store.update)
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


def interrupt_on_now_priority(engine: QueryEngine, queue: CommandQueue) -> None:
    """
    `print.ts` / `REPL.tsx`: abort the turn in flight with reason `'interrupt'` when a `'now'`
    priority command arrives (equivalent to Esc + send; 'block' tools keep running).
    """
    if engine.running and queue.get_commands_by_max_priority("now"):
        engine.interrupt("interrupt")


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


def ask_turn(
    run: DevelopmentRun, prompt: str, *, prefix: Sequence[Any] = (), is_meta: bool = False,
    user_messages: Sequence[Any] | None = None,
) -> AsyncIterator[Any]:
    """
    `print.ts`'s call to `ask()` for one prompt: the run's conversation, its read-file cache
    (cloned in, handed back), its model choice; the turn's engine becomes `run.engine`.
    """

    def set_read_file_cache(cache: dict[str, Any]) -> None:
        run.ctx.read_file_state = cache

    def take_engine(engine: QueryEngine) -> None:
        run.engine = engine

    return ask(
        prompt, config=run.config, deps=run.deps, transcript=run.transcript, session_id=run.run_id,
        context=run.ctx, mutable_messages=run.messages, get_read_file_cache=lambda: run.ctx.read_file_state,
        set_read_file_cache=set_read_file_cache, user_specified_model=run.user_specified_model,
        cost_tracker=run.ctx.cost_tracker, prefix=prefix, is_meta=is_meta, on_engine=take_engine,
        user_messages=user_messages,
    )


async def wait_for_background_work(run: DevelopmentRun) -> None:
    """
    `print.ts`'s `do … while (waitingForAgents)`: after a turn, while a background task runs or
    a main-thread command is queued, wait for the queue and submit each command as a new turn
    of the same conversation (`ask({ prompt, isMeta, mutableMessages })`). As in upstream's
    print path, a task notification goes through `processUserInput` like any prompt.
    """
    queue: CommandQueue = run.ctx.command_queue
    while True:
        command = next_main_thread_command(queue)
        if command is None:
            if not _has_running_background_task(run.store):
                return
            await asyncio.sleep(WAITING_FOR_AGENTS_POLL_SECONDS)
            continue
        logger.info("Delivering queued %s as a new turn", command.mode)
        async for event in ask_turn(run, command.value, is_meta=command.is_meta):
            if run.on_message is not None:
                run.on_message(event)


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


#: `CLAUDE_CODE_RESUME_INTERRUPTED_TURN`: auto-resume an interrupted turn on restart.
RESUME_INTERRUPTED_TURN_ENV = "TDDAGENTS_RESUME_INTERRUPTED_TURN"


async def restore_resumed_state(ctx: Any, transcript: TranscriptStore, resumed: ConversationForResume) -> None:
    """
    What a resume restores besides the messages: `copyFileHistoryForResume` (the backups of a
    previous session id, linked into this one's directory; `conversationRecovery.ts`) and
    `restoreSessionStateFromLog` (the file history state, `sessionRestore.ts`), as `print.ts`'s
    `loadInitialMessages` does after `loadConversationForResume`.
    """
    from app.utils.session_restore import restore_session_state_from_log

    bootstrap = ctx.get_app_state().bootstrap
    await copy_file_history_for_resume(resumed, bootstrap, transcript.record_file_history_snapshot)
    restore_session_state_from_log(resumed.file_history_snapshots, ctx.set_app_state, bootstrap)


async def load_initial_messages(
    ctx: Any, transcript: TranscriptStore, sub_req: str, model: str, *, resume_prompt: str | None = None
) -> tuple[Any, str]:
    """
    `print.ts` -> `loadInitialMessages` for one sub-requirement: the history the run starts
    from and the prompt it submits.

    With a transcript on disk the conversation is resumed (`loadConversationForResume`, whose
    SessionStart hooks run with source `resume`); with `TDDAGENTS_RESUME_INTERRUPTED_TURN`
    (upstream's `CLAUDE_CODE_RESUME_INTERRUPTED_TURN`) an interrupted turn is taken out and its
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
    await restore_resumed_state(ctx, transcript, resumed)
    history: list[Any] = list(resumed.messages)
    prompt = resume_prompt if resume_prompt is not None else sub_req
    interruption = resumed.turn_interruption_state
    if interruption.message is not None and is_env_truthy(os.environ.get(RESUME_INTERRUPTED_TURN_ENV)):
        logger.info("Auto-resuming interrupted turn (kind: %s)", interruption.kind)
        history = remove_interrupted_message(history, interruption.message)
        prompt = _prompt_text(interruption.message)
    session = await process_session_start_hooks(ctx.hook_dispatcher, "resume", model=model)
    session.messages[:0] = history
    return session, prompt


async def _submit(
    run: DevelopmentRun, history: Sequence[Any], prompt_text: str, prefix: Sequence[Any] = ()
) -> Terminal | None:
    """
    One user turn: `QueryEngine.submit_message` (`processUserInput`, then the loop), then any
    background work (`print.ts`'s `ask` and its wait). A turn that does not query ends
    `hook_stopped` when a UserPromptSubmit hook stopped it, else with no terminal (`None`: an
    unknown command, answered by the result's text).
    """
    run.messages[:] = list(history)
    try:
        async for event in ask_turn(run, prompt_text, prefix=prefix):
            if run.on_message is not None:
                run.on_message(event)
        processed = run.engine.input_result
        if processed is not None and not processed.should_query:
            if processed.stopped_reason is None:
                return None
            run.stopped_reason = processed.stopped_reason
            logger.info("Turn of %s not started: UserPromptSubmit hook stopped it: %s", run.run_id, run.stopped_reason)
            return Terminal.HOOK_STOPPED
        await wait_for_background_work(run)
        reason = run.engine.terminal.reason if run.engine.terminal is not None else Terminal.MODEL_ERROR
    except Exception:
        logger.exception("Loop %s crashed", run.run_id)
        reason = Terminal.MODEL_ERROR
    if run.engine.result is not None:
        logger.info("Loop %s result: %s", run.run_id, run.engine.result)
    return reason


def seed_read_file_state(run: DevelopmentRun, messages: Sequence[Any]) -> None:
    """
    `print.ts`: the read-file cache starts from what the conversation's file tools saw
    (`extractReadFilesFromMessages`), so a resumed session can edit what it already read.
    """
    from app.utils.query_helpers import extract_read_files_from_messages

    seeded = extract_read_files_from_messages(messages, run.ctx)
    run.ctx.read_file_state = {**seeded, **run.ctx.read_file_state}


async def begin_session(
    run: DevelopmentRun, fresh_prompt: str, resume_prompt: str | None = None
) -> Terminal | None:
    """
    The first turn: SessionStart and `fresh_prompt`, or — with a transcript on disk — the
    resumed conversation, continued with the interrupted prompt or else `resume_prompt`.
    """
    session, prompt_text = await load_initial_messages(
        run.ctx, run.transcript, fresh_prompt, run.config.model, resume_prompt=resume_prompt
    )
    first = [HumanMessage(content=session.initial_user_message)] if session.initial_user_message else []
    seed_read_file_state(run, session.messages)
    return await _submit(run, session.messages, prompt_text, first)


async def restore_session(run: DevelopmentRun) -> bool:
    """
    A resumed conversation loaded without a turn, as upstream's `--resume` with no prompt waits
    for the user: the history (SessionStart `resume` included) becomes the engine's, and the next
    `submit_prompt` continues it. `False` when there is no transcript to resume.
    """
    from app.utils.conversation_recovery import load_conversation_for_resume
    from app.utils.session_start import process_session_start_hooks

    resumed = load_conversation_for_resume(run.transcript.path)
    if resumed is None or not resumed.messages:
        return False
    await restore_resumed_state(run.ctx, run.transcript, resumed)
    session = await process_session_start_hooks(run.ctx.hook_dispatcher, "resume", model=run.config.model)
    run.messages[:] = [*resumed.messages, *session.messages]
    seed_read_file_state(run, run.messages)
    return True


async def submit_prompt(run: DevelopmentRun, prompt_text: str) -> Terminal | None:
    """A later turn of the same conversation: the user's prompt after everything said so far."""
    return await _submit(run, run.messages, prompt_text)


@dataclass(frozen=True, slots=True)
class DevelopmentOutcome:
    """How a development session ended: the last turn's terminal (`None` if none ran) and the plan."""

    terminal: Terminal | None
    plan: TddPlan
    #: The questions the session stopped on, with nobody to answer them (`awaiting_user_answer`).
    question: tuple[dict[str, Any], ...] | None = None
    #: The session's plan file (`app/utils/plans.py`) and transcript, for the reports.
    plan_file: str | None = None
    transcript_path: str | None = None
    #: A headless run's last stdout message (`lastMessage`): its `result`, whose `is_error` is the
    #: exit code; `None` when no turn produced one.
    result: dict[str, Any] | None = None

    @property
    def planned(self) -> bool:
        """The user approved a plan: the session has sub-requirements (`ExitPlanMode`)."""
        return bool(self.plan.items)

    @property
    def completed(self) -> bool:
        """
        There is an approved plan, every sub-requirement was observed Red -> Green -> Refactor and
        the loop ended `completed`. A session that only planned has nothing completed.
        """
        return self.planned and self.terminal == Terminal.COMPLETED and self.plan.is_complete


def first_prompts(plan: TddPlan, request: str | None, prompt: str | None) -> tuple[str, str | None]:
    """
    The session's first turn: `request` for a new session (it opens in plan mode, the Analyst);
    on resume, `prompt` (the user's next message, upstream's `--resume <id> "<prompt>"`), else the
    development resume prompt when the session has an approved plan, else nothing (`None`).
    """
    from app.session.prompts import development_resume_prompt

    resume = prompt or (development_resume_prompt(plan) if plan.items else None)
    return request or "", resume


async def start_session(run: DevelopmentRun, request: str | None, prompt: str | None) -> Terminal | None:
    """
    `begin_session` for a session that may still be planning: a resumed session with nothing to
    say is only loaded (`restore_session`) and its turn waits for the user; `None` then.
    """
    fresh, resume = first_prompts(run.store.get().tdd_plan, request, prompt)
    if resume is None and not fresh and await restore_session(run):
        return None
    return await begin_session(run, fresh, resume)


def outcome_of(run: DevelopmentRun, terminal: Terminal | None, **extra: Any) -> DevelopmentOutcome:
    """How the session ended: its terminal, its plan, and where its plan file and transcript are."""
    plans = run.ctx.session_plans
    return DevelopmentOutcome(
        terminal=terminal, plan=run.store.get().tdd_plan,
        plan_file=str(plans.file_path()) if plans is not None else None,
        transcript_path=str(run.transcript.path), **extra,
    )


@dataclass
class HeadlessOptions:
    """
    `runHeadless`'s output and input options (`--output-format`, `--input-format`, `--verbose`,
    `--include-partial-messages`, `--replay-user-messages`, `--include-hook-events`,
    `--permission-prompt-tool`). Mutable: `initialize` and `set_max_thinking_tokens` change it.
    """

    output_format: str = "text"
    input_format: str = "text"
    verbose: bool = False
    include_partial_messages: bool = False
    replay_user_messages: bool = False
    include_hook_events: bool = False
    permission_prompt_tool: str | None = None
    #: `--system-prompt` / `--append-system-prompt`, or `initialize`'s.
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    #: `thinkingConfig`: `None` (the default), `{"type": "disabled"}` or `{"type": "enabled", "budgetTokens": n}`.
    thinking_config: dict[str, Any] | None = None
    prompt_suggestions: bool = False


async def _no_input() -> AsyncIterator[str]:
    """`fromArray([])`: an input that is already closed."""
    empty: tuple[str, ...] = ()
    for block in empty:
        yield block


async def run_headless(
    session_id: str, request: str | None, *, workspace: Any, prompt: str | None = None,
    options: HeadlessOptions | None = None, input_blocks: Any = None, write_stdout: Callable[[str], None] | None = None,
    **run_options: Any,
) -> DevelopmentOutcome:
    """
    `runHeadless`: one conversation over the SDK protocol (`app/cli/structured_io.py`). Every `-p`
    run goes through it: a text prompt becomes the one user message of an input that closes at
    once (`getStructuredIO`), `--input-format stream-json` reads the user messages and control
    requests from stdin. The messages go to stdout as upstream writes them (`stream-json` with
    `--verbose`: every one; `json`: the result, or all of them with `--verbose`; `text`: the
    result's text), and the outcome carries the last one, whose `is_error` is the exit code.

    A new session opens in plan mode with `request` (the Analyst). Without a host to ask (plain
    `-p`), a permission 'ask' is denied, `ExitPlanMode` among them, so a new session plans and
    stops there; a question the model asks (`AskUserQuestion`) is denied too and the session stops
    with it (`DevelopmentOutcome.question`). Under `--input-format stream-json` (or
    `--permission-prompt-tool stdio`) every 'ask' goes to the host as `can_use_tool` (user
    decision 2026-11-01). `prompt` continues a resumed conversation, as upstream's
    `claude -p --resume <id> "<prompt>"`.
    """
    from app.cli.structured_io import StructuredIO, stdin_blocks
    from app.tools.ask_user_question_tool.ask_user_question_tool import (
        awaiting_user_answer,
    )

    options = options or HeadlessOptions()
    stream_input = options.input_format == "stream-json"
    blocks = input_blocks if input_blocks is not None else (stdin_blocks() if stream_input else _no_input())
    sio = StructuredIO(blocks, options.replay_user_messages, write_stdout=write_stdout)
    host_asks = stream_input or options.permission_prompt_tool == "stdio"
    can_use_tool = sio.create_can_use_tool() if host_asks else None
    async with open_session_run(run_id=session_run_id(session_id), plan=TddPlan(), workspace=workspace,
                                planning=True, can_use_tool=can_use_tool, **run_options) as run:
        streaming = HeadlessStreaming(sio, run, options)
        await streaming.load_initial_messages(request, prompt, stream_input=stream_input)
        needs_full_array = options.output_format == "json" and options.verbose
        messages: list[dict[str, Any]] = []
        last: dict[str, Any] | None = None
        async for message in streaming.messages():
            if options.output_format == "stream-json" and options.verbose:
                await sio.write(message)
            if is_last_message_candidate(message):
                if needs_full_array:
                    messages.append(message)
                last = message
        result = last if last is not None and last.get("type") == "result" else None
        try:
            text = final_output(options, last, messages)
        except NoMessagesReturned as error:
            logger.error("%s", error)
            text = None
        if text is not None:
            (write_stdout or _stdout_write)(text)
        return outcome_of(run, streaming.terminal, question=awaiting_user_answer(run.messages), result=result)


def _stdout_write(text: str) -> None:
    import sys

    sys.stdout.write(text)
    sys.stdout.flush()


#: The stdout message types that never become `lastMessage`.
_NOT_LAST_TYPES = frozenset({
    "control_response", "control_request", "control_cancel_request", "stream_event", "keep_alive",
    "streamlined_text", "streamlined_tool_use_summary", "prompt_suggestion",
})
#: The SDK-only `system` subtypes that never become `lastMessage`.
_NOT_LAST_SYSTEM_SUBTYPES = frozenset({
    "session_state_changed", "task_notification", "task_started", "task_progress", "post_turn_summary",
})


def is_last_message_candidate(message: Mapping[str, Any]) -> bool:
    """`runHeadless`'s filter: what may be the message the exit code and the output are read from."""
    if message.get("type") in _NOT_LAST_TYPES:
        return False
    return not (message.get("type") == "system" and message.get("subtype") in _NOT_LAST_SYSTEM_SUBTYPES)


class NoMessagesReturned(RuntimeError):
    """`throw new Error('No messages returned')`: a `json`/`text` run produced no result."""


def final_output(
    options: HeadlessOptions, last: Mapping[str, Any] | None, messages: Sequence[Mapping[str, Any]],
    *, max_turns: int | None = None, max_budget_usd: float | None = None,
) -> str | None:
    """`runHeadless`'s closing switch over the output format; `None` writes nothing (stream-json)."""
    from app.cli.ndjson_safe_stringify import ndjson_safe_stringify

    if options.output_format == "stream-json":
        return None
    if last is None or last.get("type") != "result":
        raise NoMessagesReturned("No messages returned")
    if options.output_format == "json":
        return ndjson_safe_stringify(list(messages) if options.verbose else last) + "\n"
    subtype = last.get("subtype")
    if subtype == "success":
        text = str(last.get("result") or "")
        return text if text.endswith("\n") else text + "\n"
    if subtype == "error_during_execution":
        return "Execution error"
    if subtype == "error_max_turns":
        return f"Error: Reached max turns ({max_turns})"
    if subtype == "error_max_budget_usd":
        return f"Error: Exceeded USD budget ({max_budget_usd})"
    if subtype == "error_max_structured_output_retries":
        return "Error: Failed to provide valid structured output after maximum retries"
    return None


#: `MAX_RECEIVED_UUIDS`: the user-message uuids remembered against duplicate deliveries.
MAX_RECEIVED_UUIDS = 10_000
#: The permission modes an SDK host sees in `system/status` (`setPermissionModeChangedListener`).
SDK_PERMISSION_MODES = frozenset({"default", "acceptEdits", "bypassPermissions", "plan", "auto", "dontAsk"})
#: The control requests that answer with an error in this build (user decision 2026-11-01): the
#: Anthropic account login is never impersonated, and there is no Remote Control bridge.
CLAUDE_AUTH_UNAVAILABLE = "claude.ai authentication is not available in this build"
#: `handleChannelEnable` in a build without `KAIROS` / `KAIROS_CHANNELS`.
CHANNELS_UNAVAILABLE = "channels feature not available in this build"


def can_batch_with(head: QueuedCommand, following: QueuedCommand | None) -> bool:
    """`canBatchWith`: only prompts batch, and only with the same `isMeta`."""
    return following is not None and following.mode == "prompt" and following.is_meta == head.is_meta


def join_prompt_commands(batch: Sequence[QueuedCommand]) -> QueuedCommand:
    """`joinPromptValues` over the batch, the last uuid kept: texts newline-joined, blocks concatenated."""
    if len(batch) == 1:
        return batch[0]
    head = batch[0]
    text = "\n".join(c.value for c in batch)
    blocks = tuple(b for c in batch for b in c.content_blocks)
    uuid_ = next((c.uuid for c in reversed(batch) if c.uuid), head.uuid)
    return replace(head, value=text, content_blocks=blocks, uuid=uuid_)


def prompt_from_content(content: Any) -> tuple[str, tuple[dict[str, Any], ...]]:
    """
    An SDK user message's content as a queued prompt: the text of its last text block (upstream's
    `processUserInputBase` reads the input string from it) and the blocks before it.
    """
    if isinstance(content, str):
        return content, ()
    blocks = [b for b in content or () if isinstance(b, Mapping)]
    last_text = max((i for i, b in enumerate(blocks) if b.get("type") == "text"), default=None)
    if last_text is None:
        return "", tuple(dict(b) for b in blocks)
    return str(blocks[last_text].get("text", "")), tuple(dict(b) for i, b in enumerate(blocks) if i != last_text)


def user_message_from_blocks(text: str, blocks: Sequence[Mapping[str, Any]], is_meta: bool) -> HumanMessage:
    """The user message a prompt with content blocks becomes: the blocks (images resized), then the text."""
    from app.utils.image_resizer import maybe_resize_and_downsample_image_block

    content: list[Any] = []
    for block in blocks:
        if block.get("type") == "image" and (block.get("source") or {}).get("type") == "base64":
            source = maybe_resize_and_downsample_image_block(block).block["source"]
            content.append({"type": "image", "base64": source["data"], "mime_type": source["media_type"]})
        else:
            content.append(dict(block))
    if text:
        content.append({"type": "text", "text": text})
    return HumanMessage(content=content, additional_kwargs={"is_meta": True} if is_meta else {})


def parse_task_notification(text: str) -> dict[str, Any] | None:
    """`drainCommandQueue`'s `task_notification` SDK event, read from the XML; `None` without a `<status>`."""
    import re

    def tag(name: str, source: str = text) -> str | None:
        match = re.search(rf"<{name}>([^<]+)</{name}>", source)
        return match.group(1) if match else None

    raw_status = tag("status")
    if raw_status is None:
        return None
    status = ("stopped" if raw_status == "killed" else raw_status) if raw_status in (
        "completed", "failed", "stopped", "killed") else "completed"
    usage_match = re.search(r"<usage>([\s\S]*?)</usage>", text)
    usage_text = usage_match.group(1) if usage_match else ""
    total, uses = tag("total_tokens", usage_text), tag("tool_uses", usage_text)
    duration = tag("duration_ms", usage_text)
    event: dict[str, Any] = {
        "type": "system", "subtype": "task_notification", "task_id": tag("task-id") or "",
        "status": status, "output_file": tag("output-file") or "", "summary": tag("summary") or "",
    }
    tool_use_id = tag("tool-use-id")
    if tool_use_id is not None:
        event["tool_use_id"] = tool_use_id
    if total is not None and uses is not None:
        event["usage"] = {"total_tokens": int(total), "tool_uses": int(uses),
                          "duration_ms": int(duration) if duration is not None else 0}
    return event


class HeadlessStreaming:
    """
    `runHeadlessStreaming`: two tasks over one run, as upstream's — the stdin loop (user messages
    queued, control requests answered at once) and `run()`, which drains the queue a turn at a
    time and then waits for the background tasks, feeding their notifications back as turns.
    Everything for stdout goes through `sio.outbound`; the stream ends when the input has closed
    and nothing is left to run.
    """

    def __init__(self, sio: Any, run: DevelopmentRun, options: HeadlessOptions) -> None:
        self.sio = sio
        self.run_ = run
        self.options = options
        self.output = sio.outbound
        self.session_id = run.run_id
        self.running = False
        self.input_closed = False
        self.initialized = False
        self.held_back_result: dict[str, Any] | None = None
        #: The last turn's terminal reason (`DevelopmentOutcome.terminal`).
        self.terminal: Terminal | None = None
        #: The first turn's SessionStart output (`initial_user_message`), consumed by that turn.
        self.first_prefix: Sequence[Any] = ()
        #: `pendingSeeds` (`seed_read_state`): applied at the next clone-replace boundary.
        self.pending_seeds: dict[str, Any] = {}
        self.received_uuids: dict[str, None] = {}
        self.flag_settings_inline: dict[str, Any] = {}
        self._run_task: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self._unsubscribe: list[Callable[[], None]] = []
        self._last_mode = str(run.store.get().tool_permission_context.mode)
        self._last_ledger = run.store.get().phase_ledger
        self._last_plan = run.store.get().tdd_plan
        self._synthesizer: Any = None
        self.queue: CommandQueue = run.ctx.command_queue
        self._wire()

    # ── wiring ───────────────────────────────────────────────────────────────

    def _wire(self) -> None:
        run = self.run_
        self._unsubscribe.append(run.store.subscribe(self._on_state_change))
        if run.recorder is not None:
            run.recorder.on_entry = self._on_tdd_entry
        if self.options.include_partial_messages:
            run.ctx.on_stream_chunk = self._on_stream_chunk

        async def orphan(message: dict[str, Any]) -> None:
            logger.debug("Ignoring a control_response for an unknown request: %s", message)

        self.sio.set_unexpected_response_callback(orphan)

    def _system(self, subtype: str, **fields: Any) -> dict[str, Any]:
        return {"type": "system", "subtype": subtype, **fields, "session_id": self.session_id,
                "uuid": str(uuid.uuid4())}

    def _on_state_change(self) -> None:
        """`setPermissionModeChangedListener`, and the TDD state for `tdd_ledger` / `tdd_plan`."""
        from app.utils.sdk_messages import sdk_tdd_message

        state = self.run_.store.get()
        mode = str(state.tool_permission_context.mode)
        if mode != self._last_mode:
            self._last_mode = mode
            if mode in SDK_PERMISSION_MODES:
                self.output.enqueue(self._system("status", status=None, permissionMode=mode))
        if state.phase_ledger is not self._last_ledger:
            self._last_ledger = ledger = state.phase_ledger
            self.output.enqueue(sdk_tdd_message("tdd_ledger", {
                "phase": str(ledger.phase), "tests_written": ledger.tests_written,
                "green_passed": ledger.green_passed, "refactor_passed": ledger.refactor_passed,
                "coverage": ledger.coverage,
            }, self.session_id))
        if state.tdd_plan is not self._last_plan:
            self._last_plan = plan = state.tdd_plan
            current = plan.current
            self.output.enqueue(sdk_tdd_message("tdd_plan", {
                "items": [{"id": item.id, "text": item.text, "status": str(item.status)} for item in plan.items],
                "current": current.id if current is not None else None,
            }, self.session_id))

    def _on_tdd_entry(self, entry: Any) -> None:
        """A recorded loop transition (`events.jsonl`) as `tdd_transition`."""
        from app.tdd.event_log import EventType
        from app.utils.sdk_messages import sdk_tdd_message

        if entry.event_type != EventType.TRANSITION:
            return
        data = entry.to_dict()
        data.pop("session_id", None)
        data.pop("event_type", None)
        self.output.enqueue(sdk_tdd_message("tdd_transition", data, self.session_id))

    def _on_stream_chunk(self, chunk: Any) -> None:
        """`--include-partial-messages`: the model's raw stream, as Anthropic's events."""
        from app.utils.sdk_messages import StreamEventSynthesizer, stream_event_message

        if chunk is None:
            events = self._synthesizer.finish() if self._synthesizer is not None else []
            self._synthesizer = None
        else:
            if self._synthesizer is None:
                self._synthesizer = StreamEventSynthesizer()
            events = self._synthesizer.feed(chunk)
        for event in events:
            self.output.enqueue(stream_event_message(event, self.session_id))

    # ── the initial messages ─────────────────────────────────────────────────

    async def load_initial_messages(self, request: str | None, prompt: str | None, *, stream_input: bool) -> None:
        """
        `loadInitialMessages` and `getStructuredIO`: the conversation the run starts from (SessionStart,
        or the resumed transcript), then the first prompt queued as the input's one user message. With
        stream-json input the host sends the prompts; an interrupted turn (with
        `TDDAGENTS_RESUME_INTERRUPTED_TURN`) is the only one queued here.
        """
        run = self.run_
        fresh: str
        resume: str | None
        if stream_input:
            fresh, resume = "", ""
        else:
            fresh, resume = first_prompts(run.store.get().tdd_plan, request, prompt)
        if not stream_input and resume is None and not fresh and await restore_session(run):
            return
        # Without a transcript the first prompt is the request, else the resume prompt.
        session, prompt_text = await load_initial_messages(
            run.ctx, run.transcript, fresh or resume or "", run.config.model, resume_prompt=resume)
        run.messages[:] = list(session.messages)
        seed_read_file_state(run, session.messages)
        if session.initial_user_message:
            self.first_prefix = [HumanMessage(content=session.initial_user_message)]
        if prompt_text.strip():
            self.sio.prepend_user_message(prompt_text)

    # ── the output ───────────────────────────────────────────────────────────

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """The stdout messages, in order, until the stream ends."""
        reader = asyncio.ensure_future(self._read_input())
        try:
            async for message in self.output:
                yield message
        finally:
            for unsubscribe in self._unsubscribe:
                unsubscribe()
            self.run_.ctx.on_stream_chunk = None
            for task in (reader, self._run_task, *self._background):
                if task is not None and not task.done():
                    task.cancel()

    def _kick(self) -> None:
        """
        `void run()`. `running` is set here, before the task starts, as JavaScript's `run()` sets it
        before its first `await`: the stdin loop must not end the stream under a run about to start.
        """
        if not self.running:
            self.running = True
            self._run_task = asyncio.ensure_future(self._run())

    def _spawn(self, coroutine: Any) -> None:
        """A detached handler (`void (async () => …)()`), kept alive until it finishes."""
        task = asyncio.ensure_future(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ── the stdin loop ───────────────────────────────────────────────────────

    async def _read_input(self) -> None:
        try:
            async for message in self.sio.structured_input:
                kind = message.get("type")
                if kind == "control_request":
                    if await self.handle_control_request(message):
                        break
                    continue
                if kind == "control_response":
                    if self.options.replay_user_messages:
                        self.output.enqueue(message)
                    continue
                if kind in ("assistant", "system"):
                    self._inject_history(message)
                    continue
                if kind == "user":
                    self._enqueue_user_message(message)
        except Exception as error:  # noqa: BLE001 - a broken input ends the session with its error
            logger.exception("stdin loop failed")
            self.output.enqueue(self._error_result(str(error)))
            self.output.done()
            return
        self.input_closed = True
        if not self.running:
            await self._finish()

    def _inject_history(self, message: Mapping[str, Any]) -> None:
        """A history replay (`toInternalMessages`): context the model sees, echoed with replay on."""
        from langchain_core.messages import AIMessage

        inner = message.get("message") or {}
        if message.get("type") == "assistant":
            content = inner.get("content") or ""
            text = content if isinstance(content, str) else "".join(
                str(b.get("text", "")) for b in content if isinstance(b, Mapping) and b.get("type") == "text")
            self.run_.messages.append(AIMessage(content=text))
            if self.options.replay_user_messages:
                self.output.enqueue(dict(message))

    def _enqueue_user_message(self, message: Mapping[str, Any]) -> None:
        self.initialized = True
        message_uuid = message.get("uuid")
        if isinstance(message_uuid, str) and message_uuid:
            if message_uuid in self.received_uuids:
                logger.debug("Skipping duplicate user message: %s", message_uuid)
                if self.options.replay_user_messages:
                    self.output.enqueue({"type": "user", "message": message.get("message"),
                                         "session_id": self.session_id, "parent_tool_use_id": None,
                                         "uuid": message_uuid, "timestamp": message.get("timestamp"),
                                         "isReplay": True})
                return
            self.received_uuids[message_uuid] = None
            while len(self.received_uuids) > MAX_RECEIVED_UUIDS:
                self.received_uuids.pop(next(iter(self.received_uuids)))
        text, blocks = prompt_from_content((message.get("message") or {}).get("content"))
        priority = message.get("priority")
        self.queue.enqueue(QueuedCommand(
            value=text, mode="prompt", priority=priority if priority in ("now", "next", "later") else None,
            uuid=message_uuid if isinstance(message_uuid, str) and message_uuid else str(uuid.uuid4()),
            content_blocks=blocks,
        ))
        self._kick()

    async def _finish(self) -> None:
        """The stream's end: background hooks finalized, then `output.done()`."""
        await self.run_.ctx.hook_dispatcher.registry.finalize_pending()
        self.output.done()

    # ── run() ────────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        try:
            while True:
                await self._drain_command_queue()
                has_running = _has_running_background_task(self.run_.store)
                queued = self.queue.peek(_is_main_thread) is not None
                if not (has_running or queued):
                    break
                if not queued:
                    await asyncio.sleep(WAITING_FOR_AGENTS_POLL_SECONDS)
            if self.held_back_result is not None:
                self.output.enqueue(self.held_back_result)
                self.held_back_result = None
        except Exception as error:  # noqa: BLE001 - upstream emits an error result and shuts down
            logger.exception("Headless run %s failed", self.session_id)
            self.output.enqueue(self._error_result(str(error)))
            self.terminal = Terminal.MODEL_ERROR
            self.running = False
            self.output.done()
            return
        finally:
            self.running = False
        if self.queue.peek(_is_main_thread) is not None:
            self._kick()
            return
        if self.input_closed:
            await self._finish()

    def _error_result(self, error: str) -> dict[str, Any]:
        return {
            "type": "result", "subtype": "error_during_execution", "duration_ms": 0, "duration_api_ms": 0,
            "is_error": True, "num_turns": 0, "stop_reason": None, "session_id": self.session_id,
            "total_cost_usd": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                                           "cache_creation_input_tokens": 0},
            "modelUsage": {}, "permission_denials": [], "uuid": str(uuid.uuid4()), "errors": [error],
        }

    async def _drain_command_queue(self) -> None:
        while (command := self.queue.dequeue(_is_main_thread)) is not None:
            if command.mode not in HEADLESS_COMMAND_MODES:
                raise ValueError("only prompt commands are supported in streaming mode")
            batch = [command]
            if command.mode == "prompt":
                while can_batch_with(command, self.queue.peek(_is_main_thread)):
                    following = self.queue.dequeue(_is_main_thread)
                    assert following is not None
                    batch.append(following)
                command = join_prompt_commands(batch)
                if self.options.replay_user_messages and len(batch) > 1:
                    for queued in batch:
                        if queued.uuid != command.uuid:
                            self.output.enqueue({"type": "user", "message": {"role": "user", "content": queued.value},
                                                 "session_id": self.session_id, "parent_tool_use_id": None,
                                                 "uuid": queued.uuid, "isReplay": True})
            if command.mode == "task-notification":
                event = parse_task_notification(command.value)
                if event is not None:
                    self.output.enqueue({**event, "session_id": self.session_id, "uuid": str(uuid.uuid4())})
            await self._ask(command)

    async def _ask(self, command: QueuedCommand) -> None:
        """One `ask()`: the turn's init message, its messages, its result (held back while agents run)."""
        from app.utils.sdk_messages import sdk_messages_for_event

        run = self.run_
        prefix, self.first_prefix = self.first_prefix, ()
        user_messages = None
        if command.content_blocks:
            user_messages = [user_message_from_blocks(command.value, command.content_blocks, command.is_meta)]
        self.output.enqueue(self.system_init_message())
        if self.options.replay_user_messages and command.mode == "prompt":
            content: Any = command.value if not command.content_blocks else [
                *command.content_blocks, {"type": "text", "text": command.value}]
            self.output.enqueue({"type": "user", "message": {"role": "user", "content": content},
                                 "session_id": self.session_id, "parent_tool_use_id": None, "uuid": command.uuid,
                                 "isReplay": True})
        self._apply_pending_seeds()
        async for event in ask_turn(run, command.value, prefix=prefix, is_meta=command.is_meta,
                                    user_messages=user_messages):
            if run.on_message is not None:
                run.on_message(event)
            for message in sdk_messages_for_event(event, self.session_id,
                                                  replay_user_messages=self.options.replay_user_messages):
                if message.get("type") == "result" and self._agents_running():
                    self.held_back_result = message
                    continue
                if message.get("type") == "result":
                    self.held_back_result = None
                self.output.enqueue(message)
        processed = run.engine.input_result
        if processed is not None and not processed.should_query:
            if processed.stopped_reason is not None:
                run.stopped_reason = processed.stopped_reason
                self.terminal = Terminal.HOOK_STOPPED
            return
        terminal = run.engine.terminal
        self.terminal = terminal.reason if terminal is not None else Terminal.MODEL_ERROR

    def _agents_running(self) -> bool:
        """The result is held back while a background `local_agent` runs."""
        return any(getattr(t, "type", None) in ("local_agent", "local_workflow") and is_background_task(t)
                   for t in get_running_tasks(self.run_.store.get()))

    def _apply_pending_seeds(self) -> None:
        """`setReadFileCache`'s seed merge: a seed fills a gap or replaces an older entry, once."""
        if not self.pending_seeds:
            return
        cache = dict(self.run_.ctx.read_file_state)
        for path, seed in self.pending_seeds.items():
            # `FileState` carries no timestamp here, so a seed only fills a gap.
            cache.setdefault(path, seed)
        self.run_.ctx.read_file_state = cache
        self.pending_seeds.clear()

    # ── the init message ─────────────────────────────────────────────────────

    def system_init_message(self) -> dict[str, Any]:
        """`buildSystemInitMessage` for this turn."""
        from app.commands import get_commands
        from app.utils.sdk_messages import SystemInitInputs, build_system_init_message

        run = self.run_
        ctx = run.ctx
        tools = ctx.refresh_tools() if ctx.refresh_tools is not None else ctx.tools
        registry = ctx.skill_registry
        commands = [c.user_facing_name() for c in get_commands(registry) if not c.is_hidden]
        skills = [s.name for s in registry.commands()] if registry is not None else []
        inputs = SystemInitInputs(
            cwd=str(getattr(ctx.workspace, "root", "") or ""),
            tools=[t.name for t in tools],
            mcp_servers=[(c.name, _mcp_status(c)) for c in ctx.mcp_clients],
            model=run.user_specified_model or run.config.model,
            permission_mode=str(run.store.get().tool_permission_context.mode),
            commands=commands,
            agents=sorted(self.agent_definitions()),
            skills=skills,
        )
        return build_system_init_message(inputs, self.session_id)

    def agent_definitions(self) -> dict[str, Any]:
        from app.tools.agent_tool.load_agents_dir import get_agent_definitions_with_overrides

        return get_agent_definitions_with_overrides(project_root_of(self.run_.ctx.workspace))

    # ── control requests ─────────────────────────────────────────────────────

    def respond(self, message: Mapping[str, Any], response: Mapping[str, Any] | None = None) -> None:
        """`sendControlResponseSuccess`."""
        payload: dict[str, Any] = {"subtype": "success", "request_id": message.get("request_id")}
        if response is not None:
            payload["response"] = dict(response)
        self.output.enqueue({"type": "control_response", "response": payload})

    def respond_error(self, message: Mapping[str, Any], error: str, **extra: Any) -> None:
        """`sendControlResponseError`."""
        self.output.enqueue({"type": "control_response", "response": {
            "subtype": "error", "request_id": message.get("request_id"), "error": error, **extra}})

    async def handle_control_request(self, message: Mapping[str, Any]) -> bool:
        """One inbound control request, answered; `True` ends the stdin loop (`end_session`)."""
        request = message.get("request") or {}
        subtype = str(request.get("subtype"))
        handler = getattr(self, f"_control_{subtype}", None)
        if handler is None:
            self.respond_error(message, f"Unsupported control request subtype: {subtype}")
            return False
        try:
            return bool(await handler(message, request))
        except Exception as error:  # noqa: BLE001 - a failing handler answers its request with the error
            logger.exception("control request %s failed", subtype)
            self.respond_error(message, str(error))
            return False

    def _abort_turn(self) -> None:
        engine = self.run_.engine
        if engine.running:
            engine.interrupt()

    async def _control_interrupt(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        self._abort_turn()
        self.respond(message)

    async def _control_end_session(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> bool:
        logger.debug("[print.ts] end_session received, reason=%s", request.get("reason") or "unspecified")
        self._abort_turn()
        self.respond(message)
        return True

    async def _control_initialize(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        """`handleInitializeRequest`."""
        from app.commands import format_description_with_source, get_commands

        if self.initialized:
            self.respond_error(message, "Already initialized",
                               pending_permission_requests=self.sio.get_pending_permission_requests())
            return
        if request.get("systemPrompt") is not None:
            self.options.system_prompt = self.run_.ctx.custom_system_prompt = str(request["systemPrompt"])
        if request.get("appendSystemPrompt") is not None:
            self.options.append_system_prompt = str(request["appendSystemPrompt"])
            self.run_.ctx.append_system_prompt = self.options.append_system_prompt
        if request.get("promptSuggestions") is not None:
            self.options.prompt_suggestions = bool(request["promptSuggestions"])
        agents = self.agent_definitions()
        commands = [c for c in get_commands(self.run_.ctx.skill_registry) if not c.is_hidden]
        self.respond(message, {
            "commands": [{"name": c.user_facing_name(), "description": format_description_with_source(c),
                          "argumentHint": ""} for c in commands],
            "agents": [{"name": name, "description": getattr(a, "when_to_use", ""),
                        **({"model": a.model} if getattr(a, "model", None) not in (None, "inherit") else {})}
                       for name, a in agents.items()],
            "output_style": DEFAULT_OUTPUT_STYLE,
            "available_output_styles": [DEFAULT_OUTPUT_STYLE],
            "models": [],
            "account": {"apiProvider": _api_provider(self.run_.config.model)},
            "pid": os.getpid(),
        })
        self.initialized = True
        if self.queue.peek(_is_main_thread) is not None:
            self._kick()

    async def _control_set_permission_mode(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        """`handleSetPermissionMode`."""
        from app.utils.settings.settings import is_bypass_permissions_mode_disabled, load_settings

        mode = str(request.get("mode"))
        try:
            to_mode = PermissionMode(mode)
        except ValueError:
            self.respond_error(message, f"Invalid permission mode: {mode}")
            return
        context = self.run_.store.get().tool_permission_context
        if to_mode == PermissionMode.BYPASS_PERMISSIONS:
            locations = run_settings_locations(self.run_.config, project_root_of(self.run_.ctx.workspace), None, None)
            if is_bypass_permissions_mode_disabled(load_settings(locations)):
                self.respond_error(message, "Cannot set permission mode to bypassPermissions because it is "
                                            "disabled by settings or configuration")
                return
            if not context.is_bypass_permissions_mode_available:
                self.respond_error(message, "Cannot set permission mode to bypassPermissions because the session "
                                            "was not launched with --dangerously-skip-permissions")
                return
        self.respond(message, {"mode": mode})
        self.run_.store.update(lambda state: set_permission_mode(state, to_mode))
        self.run_.ctx.tools = self.run_.ctx.refresh_tools()

    async def _control_set_model(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        requested = request.get("model") or "default"
        model = self.run_.config.model if requested == "default" else str(requested)
        self.run_.user_specified_model = model
        self.respond(message)

    async def _control_set_max_thinking_tokens(
        self, message: Mapping[str, Any], request: Mapping[str, Any]
    ) -> None:
        tokens = request.get("max_thinking_tokens")
        if tokens is None:
            self.options.thinking_config = None
        elif tokens == 0:
            self.options.thinking_config = {"type": "disabled"}
        else:
            self.options.thinking_config = {"type": "enabled", "budgetTokens": int(tokens)}
        self.respond(message)

    async def _control_mcp_status(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        self.respond(message, {"mcpServers": build_mcp_server_statuses(self.run_.ctx.mcp_clients)})

    async def _control_cancel_async_message(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        target = request.get("message_uuid")
        removed = self.queue.dequeue_all_matching(lambda command: command.uuid == target)
        self.respond(message, {"cancelled": len(removed) > 0})

    async def _control_seed_read_state(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        """A file the host saw read, seeded into the read cache when the disk has not changed since."""
        from app.utils.file_state_cache import FileState
        from app.utils.path import expand_path

        try:
            path = expand_path(str(request.get("path")), self.run_.ctx)
            disk_mtime = int(os.stat(path).st_mtime * 1000)
            if disk_mtime <= int(request.get("mtime") or 0):
                with open(path, encoding="utf-8") as handle:
                    raw = handle.read()
                content = (raw[1:] if raw.startswith("﻿") else raw).replace("\r\n", "\n")
                self.pending_seeds[path] = FileState(content=content)
        except (OSError, ValueError, UnicodeDecodeError):
            pass
        self.respond(message)

    async def _control_stop_task(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        from app.tasks.stop_task import stop_task

        await stop_task(str(request.get("task_id")), self.run_.store.get, self.run_.store.update)
        self.respond(message, {})

    async def _control_get_settings(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        """`getSettingsWithSources` plus what is applied."""
        from app.utils.settings.settings import (
            SETTING_SOURCES,
            get_settings_for_source,
            load_settings,
            merge_settings,
        )

        run = self.run_
        locations = run_settings_locations(run.config, project_root_of(run.ctx.workspace), None, None)
        sources = []
        for source in SETTING_SOURCES:
            settings = get_settings_for_source(source, locations) or {}
            if source == "flagSettings" and self.flag_settings_inline:
                settings = merge_settings(settings, self.flag_settings_inline)
            if settings:
                sources.append({"source": str(source), "settings": settings})
        effective = merge_settings(load_settings(locations), self.flag_settings_inline)
        self.respond(message, {"effective": effective, "sources": sources,
                               "applied": {"model": run.user_specified_model or run.config.model, "effort": None}})

    async def _control_apply_flag_settings(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        """
        The provided settings shallow-merged into the inline flag settings (`null` clears a key);
        a `model` key changes the model at once.
        """
        incoming = dict(request.get("settings") or {})
        merged = {**self.flag_settings_inline, **incoming}
        self.flag_settings_inline = {k: v for k, v in merged.items() if v is not None}
        if "model" in incoming:
            self.run_.user_specified_model = str(incoming["model"]) if incoming["model"] is not None else None
        self.respond(message)

    async def _control_claude_authenticate(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        self.respond_error(message, CLAUDE_AUTH_UNAVAILABLE)

    async def _control_claude_oauth_callback(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        self.respond_error(message, "No active claude_authenticate flow")

    async def _control_claude_oauth_wait_for_completion(
        self, message: Mapping[str, Any], request: Mapping[str, Any]
    ) -> None:
        self.respond_error(message, "No active claude_authenticate flow")

    async def _control_channel_enable(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        self.respond_error(message, CHANNELS_UNAVAILABLE)

    async def _control_remote_control(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        if request.get("enabled"):
            self.respond_error(message, "Remote Control initialization failed")
        else:
            self.respond(message)


#: `DEFAULT_OUTPUT_STYLE_NAME`.
DEFAULT_OUTPUT_STYLE = "default"


def _api_provider(model: str) -> str:
    """`getAPIProvider`: `firstParty` for a Claude model, the chat model's provider otherwise."""
    return "firstParty" if model.startswith("claude") else "openai"


def _mcp_status(client: Any) -> str:
    """A connection's status as the SDK names it."""
    kind = str(getattr(client, "type", "pending"))
    return {"disabled": "disabled", "connected": "connected", "failed": "failed", "needs-auth": "needs-auth"}.get(
        kind, "pending")


def build_mcp_server_statuses(clients: Sequence[Any]) -> list[dict[str, Any]]:
    """`buildMcpServerStatuses`: each server's name, status, and what it serves."""
    statuses = []
    for client in clients:
        status: dict[str, Any] = {"name": client.name, "status": _mcp_status(client)}
        config = getattr(client, "config", None)
        if config is not None:
            status["config"] = {k: v for k, v in vars(config).items() if v is not None} if hasattr(
                config, "__dict__") else {}
        if getattr(client, "type", None) == "failed" and getattr(client, "error", None):
            status["error"] = client.error
        tools = getattr(client, "tools", None)
        if tools:
            status["tools"] = [{"name": getattr(t, "name", str(t))} for t in tools]
        statuses.append(status)
    return statuses


async def run_rewind_files(
    session_id: str,
    user_message_id: str,
    *,
    workspace: Any,
    write_out: Callable[[str], Any],
    write_err: Callable[[str], Any],
    **run_options: Any,
) -> int:
    """
    `print.ts`'s `--rewind-files <user-message-id>`: a standalone operation on a resumed session.
    The session is loaded as for a turn (`loadInitialMessages`, its file history restored), the
    files are put back as they were when that user message was sent (`handleRewindFiles`), and
    the exit code is returned: 0 when rewound, 1 otherwise, with upstream's messages.
    """
    from app.utils.session_storage import entry_type_of, message_uuid

    async with open_session_run(run_id=session_run_id(session_id), plan=TddPlan(), workspace=workspace,
                                **run_options) as run:
        await restore_session(run)
        # File history snapshots are only created for user messages, so the target must be one.
        target = next((m for m in run.messages if message_uuid(m) == user_message_id), None)
        if target is None or entry_type_of(target) != "user":
            write_err(f"Error: --rewind-files requires a user message UUID, but {user_message_id} is not a user "
                      "message in this session\n")
            return 1
        result = await handle_rewind_files(user_message_id, run.store.get(), run.store.update, False,
                                           workspace=run.ctx.workspace)
        if not result.can_rewind:
            write_err(f"Error: {result.error or 'Unexpected error'}\n")
            return 1
        write_out(f"Files rewound to state at message {user_message_id}\n")
        return 0


__all__ = [
    "DevelopmentOutcome",
    "DevelopmentRun",
    "ask_turn",
    "begin_session",
    "build_builtin_tools",
    "coordinator_context",
    "first_prompts",
    "interrupt_on_now_priority",
    "load_initial_messages",
    "main_thread_tools",
    "next_main_thread_command",
    "open_session_run",
    "outcome_of",
    "project_root_of",
    "restore_session",
    "run_agent_memory_store",
    "HeadlessOptions",
    "HeadlessStreaming",
    "NoMessagesReturned",
    "build_mcp_server_statuses",
    "can_batch_with",
    "final_output",
    "is_last_message_candidate",
    "join_prompt_commands",
    "parse_task_notification",
    "prompt_from_content",
    "run_headless",
    "restore_resumed_state",
    "run_permission_context",
    "run_rewind_files",
    "run_settings_locations",
    "seed_read_file_state",
    "session_run_id",
    "session_workspace",
    "start_session",
    "submit_prompt",
    "wait_for_background_work",
]
