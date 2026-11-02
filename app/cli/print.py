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
import json
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, NoReturn

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
from app.utils.plugins.plugin_loader import SYNC_PLUGIN_INSTALL_ENV
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
    extra_agents: Mapping[str, Any] | None = None,
    plugin_agents: Sequence[Any] = (),
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
    `assemble_tool_pool(mcp_tools=…)`. The enabled plugins' agents (`plugin_agents`) sit between the
    built-in agents and the user's. `EnterPlanMode` and `ExitPlanMode` are the main thread's
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
    definitions = get_agent_definitions_with_overrides(project_dir=project_root, user_home=home,
                                                       plugin_agents=plugin_agents)
    # `--agents` / `initialize.agents` (`flagSettings`) override the user's and the project's.
    definitions.update(extra_agents or {})
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


class NoConversationFound(LookupError):
    """`No conversation found ...`: `--continue` or `--resume` named nothing to load."""


def thread_transcript(thread_id: str) -> Path:
    """The thread's transcript path (its project resolved, a fork's link followed), creating nothing."""
    from app.workspace.local import PROJECT_DIRNAME, workspaces_root

    project = (workspaces_root() / thread_id / PROJECT_DIRNAME).expanduser().resolve()
    return TranscriptStore(session_run_id(thread_id), str(project)).path


def most_recent_thread() -> str | None:
    """
    `--continue` (user decision 2026-11-01): the thread whose transcript changed last, across every
    session, since a session's project is its own folder rather than the current directory.
    """
    from app.workspace.local import workspaces_root

    root = workspaces_root()
    if not root.is_dir():
        return None
    latest: tuple[float, str] | None = None
    for thread_dir in root.iterdir():
        if not (thread_dir / "project").exists():
            continue
        transcript = thread_transcript(thread_dir.name)
        if transcript.exists():
            stamp = (transcript.stat().st_mtime, thread_dir.name)
            latest = stamp if latest is None or stamp > latest else latest
    return latest[1] if latest is not None else None


def fork_thread(source: str, target: str, plans_dir: Path | None = None) -> None:
    """
    `--fork-session`, as upstream (user decision 2026-11-01): a new thread over the SAME project
    folder (its `project` is a link to the source's), with the conversation re-recorded under the
    new id, the TDD plan sidecar copied, and the plan file copied under a new slug (`copyPlanForFork`).
    """
    from app.utils.plans import PLAN_SLUG_FILE_NAME, copy_plan_for_fork, get_plans_directory
    from app.utils.session_storage import fork_session_transcript
    from app.utils.session_storage_portable import get_project_dir
    from app.workspace.local import workspaces_root

    source_transcript = thread_transcript(source)
    if not source_transcript.exists():
        raise NoConversationFound(f"No conversation found with session ID: {source}")
    project = LocalWorkspace.for_run(source).root
    target_dir = workspaces_root() / target
    target_dir.mkdir(parents=True, exist_ok=True)
    link = target_dir / "project"
    if not link.exists():
        link.symlink_to(project, target_is_directory=True)
    old_run, new_run = session_run_id(source), session_run_id(target)
    fork_session_transcript(source_transcript, TranscriptStore(new_run, str(project)).path, new_run)
    sessions = get_project_dir(str(project))
    old_plan, new_plan = plan_path(old_run, str(project)), plan_path(new_run, str(project))
    if old_plan.exists():
        new_plan.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(old_plan, new_plan)
    if (sessions / old_run / PLAN_SLUG_FILE_NAME).exists():
        directory = plans_dir if plans_dir is not None else get_plans_directory({}, project, config_home())
        copy_plan_for_fork(sessions / old_run, sessions / new_run, directory)


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


#: The fire-and-forget plugin bookkeeping tasks (upstream's `void` promises), kept referenced.
_PLUGIN_STARTUP_TASKS: set[asyncio.Task[None]] = set()


def _spawn_plugin_task(coro: Any) -> None:
    task: asyncio.Task[None] = asyncio.ensure_future(coro)
    _PLUGIN_STARTUP_TASKS.add(task)
    task.add_done_callback(_PLUGIN_STARTUP_TASKS.discard)


async def initialize_plugins_at_startup(locations: SettingsLocations, *, interactive: bool) -> None:
    """
    `main.tsx`'s plugin bookkeeping at startup: sync the versioned plugin registry
    (`initializeVersionedPlugins`), then garbage-collect orphaned plugin versions
    (`cleanupOrphanedPluginVersionsInBackground`). A non-interactive run awaits the sync and
    leaves the cleanup in the background; an interactive one runs both in the background.
    Upstream then warms the Grep/Glob exclusions (`getGlobExclusionsForPluginCache`); here they
    are computed on first use instead (a ripgrep scan at every session start buys nothing yet).
    """
    from app.utils.plugins.cache_utils import cleanup_orphaned_plugin_versions_in_background
    from app.utils.plugins.installed_plugins_manager import initialize_versioned_plugins

    async def initialize_then_cleanup() -> None:
        await initialize_versioned_plugins(locations)
        await cleanup_orphaned_plugin_versions_in_background()

    try:
        if interactive:
            _spawn_plugin_task(initialize_then_cleanup())
        else:
            await initialize_versioned_plugins(locations)
            _spawn_plugin_task(cleanup_orphaned_plugin_versions_in_background())
    except Exception as exc:  # noqa: BLE001 - plugin bookkeeping never stops a session from opening
        logger.error("Plugin startup bookkeeping failed: %s", exc)


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


async def load_plugin_commands_into(skill_registry: Any, locations: SettingsLocations) -> None:
    """
    `getSkills` / `loadAllCommands`' plugin half: the enabled plugins' commands, then their skills,
    swapped into the run's skill registry. A failure leaves the run without them (logged), as
    upstream's `getPluginSkills().catch(...)`.
    """
    from app.utils.plugins.load_plugin_commands import get_all_plugin_commands

    try:
        skill_registry.set_plugin_commands(await get_all_plugin_commands(locations))
    except Exception as exc:  # noqa: BLE001 - plugin commands are non-critical, as upstream
        logger.error("Plugin commands failed to load, continuing without them: %s", exc)


async def _nothing() -> None:
    return None


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
    #: `--max-turns` / `--max-budget-usd` for every `ask` (opt-in; `None` is unbounded).
    max_turns: int | None = None
    max_budget_usd: float | None = None
    #: `--json-schema` (or `initialize.jsonSchema`): the structured output every `ask` asks for.
    json_schema: dict[str, Any] | None = None
    #: The pool's post-filter additions (`StructuredOutput`); `refresh_tools` appends them.
    session_tools: list[Any] = field(default_factory=list)
    #: Every MCP tool the run connected (`appState.mcp.tools`).
    mcp_tools: list[Any] = field(default_factory=list)
    #: The session's live MCP servers (`app/cli/session_mcp.py`).
    mcp: Any = None
    #: The session's `flagSettings` agents (`--agents`, `initialize.agents`), and how to add more.
    extra_agents: dict[str, Any] = field(default_factory=dict)
    add_agent_definitions: Callable[[Mapping[str, Any]], None] | None = None
    #: The run's settings sources (`run_settings_locations`).
    settings_locations: SettingsLocations | None = None
    #: `appState.agentDefinitions`: built-in < plugin < user < project < `flagSettings`.
    get_agent_definitions: Callable[[], dict[str, Any]] | None = None
    #: `refreshActivePlugins` applied to this run (`/reload-plugins`, `reload_plugins`); see `open_session_run`.
    refresh_plugins: Callable[..., Awaitable[Any]] | None = None
    #: `startBackgroundHousekeeping` ran (once per session).
    housekeeping_started: bool = False

    def start_housekeeping(self) -> None:
        """`startBackgroundHousekeeping`, once: the plugin autoupdate in the background."""
        if self.housekeeping_started or self.settings_locations is None:
            return
        self.housekeeping_started = True
        from app.utils.background_housekeeping import start_background_housekeeping

        task = start_background_housekeeping(self.settings_locations)
        _PLUGIN_STARTUP_TASKS.add(task)
        task.add_done_callback(_PLUGIN_STARTUP_TASKS.discard)


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
    interactive: bool | None = None,
    thinking: str | None = None,
    max_thinking_tokens: int | None = None,
    agents: Mapping[str, Any] | None = None,
    hidden_tool_names: Sequence[str] = (),
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
    # `getIsNonInteractiveSession`: every `-p` run is non-interactive, a host answering its asks or not.
    is_interactive = interactive if interactive is not None else can_use_tool is not None
    bootstrap = SessionBootstrap(session_id=run_id, original_cwd=cwd, is_interactive=is_interactive)
    store = AppStateStore(state=AppState(tool_permission_context=perm, tdd_plan=plan, bootstrap=bootstrap))
    from app.skills.load_skills_dir import get_skill_registry

    # `getSkills`, once per run: bundled, policy, user and the project dirs up to home.
    skill_registry = get_skill_registry(root_dir, home, managed_dir, model=config.model)
    locations = run_settings_locations(config, root_dir, home, managed_dir)
    await initialize_plugins_at_startup(locations, interactive=is_interactive)
    # `loadAllCommands`' plugin commands and skills, and `loadPluginAgents`, from the plugins on disk.
    await load_plugin_commands_into(skill_registry, locations)
    from app.utils.plugins.load_plugin_agents import load_plugin_agents

    plugin_agents: list[Any] = await load_plugin_agents(locations)
    from app.cli.session_mcp import SessionMcp

    #: The session's live MCP servers (configured, SDK, dynamic): the pools read them at each refresh.
    session_mcp = SessionMcp(manager=None, cwd=str(root_dir),
                             settings_locations=lambda: run_settings_locations(config, root_dir, home, managed_dir))
    session_mcp.connect_options = session_mcp.elicitation_options()
    mcp = await connect_mcp_servers(
        root_dir, run_settings_locations(config, root_dir, home, managed_dir), **session_mcp.connect_options,
        # The auth tool's reconnect swapped a server's client and tools in the manager's lists.
        on_reconnect=lambda _name, _result: session_mcp.on_change())
    session_mcp.manager = mcp
    try:
        coordinator = is_coordinator_mode(config)
        #: `--agents` / SDK `initialize.agents`: the session's `flagSettings` agents.
        extra_agents: dict[str, Any] = dict(agents or {})
        built_ins: list[Tool] = []
        planning_tools: list[Tool] = []

        def build_rosters() -> None:
            nonlocal built_ins, planning_tools
            mcp_tools = session_mcp.all_tools()
            built_ins = main_thread_tools(
                config, deps, root_dir, home, skill_registry=skill_registry, mcp_tools=mcp_tools,
                extra_agents=extra_agents, plugin_agents=plugin_agents)
            planning_tools = (
                main_thread_tools(config, deps, root_dir, home, skill_registry=skill_registry, mcp_tools=mcp_tools,
                                  coordinator_filter=False, extra_agents=extra_agents, plugin_agents=plugin_agents)
                if coordinator else built_ins
            )

        build_rosters()
        pool_cache: dict[str, Any] = {"key": None, "tools": ()}
        #: Tools added after the pool's filtering, as `main.tsx` adds `StructuredOutput`.
        session_tools: list[Tool] = []

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
            all_mcp = session_mcp.all_tools()
            key = (current.phase if current is not None else None, in_plan, permission_context.always_deny_rules,
                   tuple(id(tool) for tool in session_tools), tuple(id(tool) for tool in all_mcp))
            if key != pool_cache["key"]:
                pool_cache["key"] = key
                # `mergeAndFilterTools`: the coordinator filter runs over the merged pool, MCP included.
                main_mcp = apply_coordinator_tool_filter(all_mcp) if coordinator else all_mcp
                tools, extra = (planning_tools, all_mcp) if in_plan else (built_ins, main_mcp)
                # `--permission-prompt-tool`: the tool that answers permission asks is never the model's.
                assembled = assemble_tool_pool(
                    tools, extra, phase_ledger=current, permission_context=permission_context)
                pool_cache["tools"] = (*(t for t in assembled if t.name not in hidden_tool_names), *session_tools)
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
        session_mcp.hook_dispatcher = hook_dispatcher

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
            mcp_clients=tuple(session_mcp.all_clients()),
            **coordinator_context(config),
        )
        ctx.can_use_tool = can_use_tool
        ctx.custom_system_prompt = custom_system_prompt
        ctx.append_system_prompt = append_system_prompt
        from app.utils.thinking import resolve_thinking_config

        ctx.thinking_config = resolve_thinking_config(
            thinking, max_thinking_tokens, load_settings(run_settings_locations(config, root_dir, home, managed_dir)))
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

        def add_agent_definitions(new: Mapping[str, Any]) -> None:
            """`initialize.agents`: merged into the rosters, the Agent tool rebuilt with them."""
            extra_agents.update(new)
            build_rosters()
            pool_cache["key"] = None
            ctx.tools = refresh_tools()

        def on_mcp_change() -> None:
            """An MCP server came, went or reconnected: the rosters and the pool are rebuilt."""
            build_rosters()
            pool_cache["key"] = None
            ctx.mcp_clients = tuple(session_mcp.all_clients())
            ctx.tools = refresh_tools()
            run.mcp_tools = session_mcp.all_tools()

        session_mcp.on_change = on_mcp_change

        def agent_definitions() -> dict[str, Any]:
            """`appState.agentDefinitions` with the `flagSettings` agents on top."""
            from app.tools.agent_tool.load_agents_dir import (
                get_agent_definitions_with_overrides,
            )

            definitions = get_agent_definitions_with_overrides(project_dir=root_dir, user_home=home,
                                                               plugin_agents=plugin_agents)
            definitions.update(extra_agents)
            return definitions

        async def refresh_plugins(*, mcp: bool = True) -> Any:
            """
            `refreshActivePlugins` for this run, then what upstream re-reads after it: the command
            list (`getCommands`, the plugin commands and skills), the sent-skill memory
            (`resetSentSkillNames`), the plugin agents in the rosters, and — unless `mcp` is off —
            the plugin MCP servers (`applyPluginMcpDiff` / `pluginReconnectKey`). A failure after
            the refresh is logged, never raised, as upstream's `allSettled`.
            """
            from app.utils.plugins.refresh import refresh_active_plugins

            result = await refresh_active_plugins(store.update, locations, hook_dispatcher=hook_dispatcher)
            plugin_agents[:] = [a for a in result.agent_definitions.values() if getattr(a, "source", None) == "plugin"]
            skill_registry.sent_skill_names.clear()
            outcomes = await asyncio.gather(
                load_plugin_commands_into(skill_registry, locations),
                session_mcp.refresh_plugin_servers() if mcp else _nothing(),
                return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    logger.error("Plugin refresh: %s", outcome)
            build_rosters()
            pool_cache["key"] = None
            ctx.tools = refresh_tools()
            return result

        run = DevelopmentRun(run_id=run_id, config=config, store=store, ctx=ctx, engine=engine,
                             add_agent_definitions=add_agent_definitions, extra_agents=extra_agents,
                             mcp_tools=session_mcp.all_tools(), mcp=session_mcp,
                             session_tools=session_tools,
                             transcript=transcript, deps=run_deps, messages=engine.mutable_messages,
                             recorder=recorder, settings_locations=locations,
                             get_agent_definitions=agent_definitions, refresh_plugins=refresh_plugins)
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
        await session_mcp.aclose()
        await mcp.aclose()


def interrupt_on_now_priority(engine: QueryEngine, queue: CommandQueue) -> None:
    """
    `print.ts` / `REPL.tsx`: abort the turn in flight with reason `'interrupt'` when a `'now'`
    priority command arrives (equivalent to Esc + send; 'block' tools keep running).
    """
    if engine.running and queue.get_commands_by_max_priority("now"):
        engine.interrupt("interrupt")


async def connect_mcp_servers(root_dir: Path, locations: SettingsLocations, **manager_options: Any) -> Any:
    """
    `getClaudeCodeMcpConfigs` + `getMcpToolsCommandsAndResources` for the run: every configured
    server connected (a server that fails is recorded, never fatal), headless as `claude -p`,
    so project servers load without an approval prompt. `manager_options` are
    `McpConnectionManager` fields (the elicitation callbacks, `on_reconnect`).
    """
    from app.services.mcp.config import get_claude_code_mcp_configs
    from app.services.mcp.mcp_connection_manager import McpConnectionManager

    configs, errors = await get_claude_code_mcp_configs(root_dir, locations)
    for error in errors:
        logger.warning("MCP config error: %s", error)
    manager = McpConnectionManager(cwd=str(root_dir), **manager_options)
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
        user_messages=user_messages, max_turns=run.max_turns, max_budget_usd=run.max_budget_usd,
        json_schema=run.json_schema,
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
    run.start_housekeeping()
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
    """
    A later turn of the same conversation: the user's prompt after everything said so far. The
    first one starts the background housekeeping (`REPL.tsx`'s `submitCount === 1`).
    """
    run.start_housekeeping()
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
    #: `--max-turns` / `--max-budget-usd` (opt-in ceilings, user decision 2026-11-01).
    max_turns: int | None = None
    max_budget_usd: float | None = None
    #: `--json-schema`: the final answer as JSON matching it (`StructuredOutput`).
    json_schema: dict[str, Any] | None = None


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
    can_use_tool: Any = None
    run_ref: list[DevelopmentRun] = []
    prompt_tool = options.permission_prompt_tool
    if prompt_tool and prompt_tool != "stdio":
        can_use_tool = get_permission_prompt_tool_can_use_tool(prompt_tool, lambda: run_ref[0].mcp_tools)
        run_options = {**run_options, "hidden_tool_names": (prompt_tool,)}
    if host_asks:
        # `onPermissionPrompt`: the session waits on the host (`requires_action`) until it answers.
        def on_permission_prompt(details: Mapping[str, Any]) -> None:
            if streaming_ref:
                streaming_ref[0]._session_state("requires_action")

        def on_permission_state(details: Any) -> None:
            if streaming_ref and details is None:
                streaming_ref[0]._session_state("running")

        sio.on_permission_state = on_permission_state
        can_use_tool = sio.create_can_use_tool(on_permission_prompt)
    streaming_ref: list[HeadlessStreaming] = []
    async with open_session_run(run_id=session_run_id(session_id), plan=TddPlan(), workspace=workspace,
                                planning=True, can_use_tool=can_use_tool, interactive=False,
                                **run_options) as run:
        run.max_turns, run.max_budget_usd = options.max_turns, options.max_budget_usd
        if options.json_schema is not None:
            use_json_schema(run, options.json_schema)
        run_ref.append(run)
        streaming = HeadlessStreaming(sio, run, options, session_id)
        streaming_ref.append(streaming)
        # `print.ts`: the background plugin installation, then `main.tsx`'s housekeeping once MCP is up.
        streaming.start_plugin_install()
        run.start_housekeeping()
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
            text = final_output(options, last, messages, max_turns=options.max_turns,
                                max_budget_usd=options.max_budget_usd)
        except NoMessagesReturned as error:
            logger.error("%s", error)
            text = None
        if text is not None:
            (write_stdout or _stdout_write)(text)
        return outcome_of(run, streaming.terminal, question=awaiting_user_answer(run.messages), result=result)


def use_json_schema(run: DevelopmentRun, json_schema: Mapping[str, Any]) -> bool:
    """
    `main.tsx` / `print.ts`: `StructuredOutput` for `json_schema` joins the pool after its filtering,
    and every later `ask` asks for it. An invalid schema adds nothing (logged), as upstream.
    """
    from app.tools.synthetic_output_tool.synthetic_output_tool import create_synthetic_output_tool

    tool = create_synthetic_output_tool(json_schema)
    if isinstance(tool, str):
        logger.error("Invalid JSON schema: %s", tool)
        return False
    run.json_schema = dict(json_schema)
    run.session_tools.append(tool)
    run.ctx.tools = run.ctx.refresh_tools()
    return True


class PermissionPromptToolError(RuntimeError):
    """`--permission-prompt-tool` names no MCP tool (upstream exits 1 with this message)."""


#: The decision when the turn is aborted while the prompt tool decides.
PERMISSION_PROMPT_ABORTED = "Permission prompt was aborted."


def create_can_use_tool_with_permission_prompt(prompt_tool: Any) -> Callable[..., Any]:
    """
    `createCanUseToolWithPermissionPrompt`: an 'ask' is answered by an MCP tool, called with
    `{tool_name, input, tool_use_id}` and raced against the turn's abort; its text is the JSON of an
    `allow` / `deny` (`PermissionPromptToolResultSchema`). **TDDAgents**: a changed input is re-checked
    against the rule-based gate (the TDD phase tier among it), as a host's answer is.
    """
    from app.cli.structured_io import _decision_from_host
    from app.types.permissions import PermissionBehavior, PermissionResult
    from app.utils.permissions.permission_prompt_tool_result_schema import parse_permission_prompt_output

    def aborted() -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.DENY, message=PERMISSION_PROMPT_ABORTED, decision_reason={
            "type": "permissionPromptTool", "permissionPromptToolName": prompt_tool.name, "toolResult": None})

    async def can_use_tool(tool: Any, input_args: dict[str, Any], result: Any, context: Any,
                           tool_use_id: str) -> Any:
        if result.behavior in (PermissionBehavior.ALLOW, PermissionBehavior.DENY):
            return result
        if context.cancel.cancelled:
            return aborted()
        abort: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        def on_abort() -> None:
            if not abort.done():
                abort.set_result("aborted")

        remove = context.cancel.add_listener(on_abort)
        answer: asyncio.Future[str] = asyncio.ensure_future(_call_tool(
            prompt_tool, {"tool_name": tool.name, "input": input_args, "tool_use_id": tool_use_id}, context))
        try:
            await asyncio.wait([answer, abort], return_when=asyncio.FIRST_COMPLETED)
        finally:
            remove()
        if not answer.done() or context.cancel.cancelled:
            answer.cancel()
            return aborted()
        text = answer.result()
        if not isinstance(text, str) or not text:
            raise PermissionPromptToolError(
                'Permission prompt tool returned an invalid result. Expected a single text block param with '
                'type="text" and a string text value.')
        parsed = parse_permission_prompt_output(json.loads(text))
        return await _decision_from_host(parsed, prompt_tool, input_args, context)

    return can_use_tool


async def _call_tool(tool: Tool, args: dict[str, Any], context: Any) -> str:
    called = tool.call(args, context)
    result = await called if asyncio.iscoroutine(called) else called
    return str(getattr(result, "content", result))


def _exit_with(error: str) -> NoReturn:
    """`process.stderr.write(error); gracefulShutdownSync(1)`."""
    import sys

    sys.stderr.write(f"{error}\n")
    raise SystemExit(1)


def get_permission_prompt_tool_can_use_tool(
    name: str, get_mcp_tools: Callable[[], Sequence[Any]]
) -> Callable[..., Any]:
    """
    `getCanUseToolFn` for an MCP tool name: looked up at the first ask (MCP servers may still be
    connecting at startup); a missing or non-MCP tool ends the run with upstream's error.
    """
    from app.tool import tool_matches_name

    resolved: list[Callable[..., Any]] = []

    async def can_use_tool(*args: Any) -> Any:
        if not resolved:
            tools = list(get_mcp_tools())
            found = next((t for t in tools if tool_matches_name(t, name)), None)
            if found is None:
                available = ", ".join(t.name for t in tools) or "none"
                _exit_with(f"Error: MCP tool {name} (passed via --permission-prompt-tool) not found. "
                           f"Available MCP tools: {available}")
            if not getattr(found, "is_mcp", False):
                _exit_with(f"Error: tool {name} (passed via --permission-prompt-tool) must be an MCP tool")
            resolved.append(create_can_use_tool_with_permission_prompt(found))
        return await resolved[0](*args)

    return can_use_tool


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

    def __init__(self, sio: Any, run: DevelopmentRun, options: HeadlessOptions, session_id: str | None = None) -> None:
        self.sio = sio
        self.run_ = run
        self.options = options
        self.output = sio.outbound
        #: The SDK's `session_id`: the thread id, the one `--thread-id` / `--resume` take (user decision
        #: 2026-11-01); the run id (`loop-…`) names the transcript, not the session.
        self.session_id = session_id or run.run_id
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
        from app.cli.mcp_auth_control import McpOAuthFlows

        #: The session's in-flight MCP OAuth flows (`activeOAuthFlows`, `oauthCallbackSubmitters`, …).
        self.oauth_flows = McpOAuthFlows()
        self._run_task: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        #: `pluginInstallPromise`: the sync-mode install `run()` awaits before the first turn.
        self.plugin_install: asyncio.Task[None] | None = None
        #: The fire-and-forget plugin install (`void installPluginsAndApplyMcpInBackground()`).
        self._plugin_tasks: set[asyncio.Task[None]] = set()
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
        if run.mcp is not None:
            # `setupSdkMcpClients(…, (name, message) => structuredIO.sendMcpMessage(name, message))`.
            run.mcp.send_mcp_message = self.sio.send_mcp_message
            # `registerElicitationHandlers(…)`: the host answers, `system/elicitation_complete` is streamed.
            run.mcp.ask_host = self._ask_host_elicitation
            run.mcp.emit_elicitation_complete = lambda server, elicitation_id: self.output.enqueue(self._system(
                "elicitation_complete", mcp_server_name=server, elicitation_id=elicitation_id))
        dispatcher = run.ctx.hook_dispatcher
        if dispatcher is not None and self.options.output_format == "stream-json" and self.options.verbose:
            # `print.ts`: hook lifecycle events reach the stream; every event only with --include-hook-events.
            dispatcher.events.all_hook_events_enabled = self.options.include_hook_events
            dispatcher.events.register_handler(self._on_hook_event)
            self._unsubscribe.append(lambda: dispatcher.events.register_handler(None))

        async def orphan(message: dict[str, Any]) -> None:
            logger.debug("Ignoring a control_response for an unknown request: %s", message)

        self.sio.set_unexpected_response_callback(orphan)

    def _drain_sdk_events(self) -> None:
        """`drainSdkEvents`: the queued task and session-state events, onto the stream."""
        for event in self.run_.store.get().sdk_events.drain(self.session_id):
            self.output.enqueue(event)

    def _session_state(self, session_state: str) -> None:
        from app.utils.sdk_event_queue import notify_session_state_changed

        notify_session_state_changed(self.run_.store.get(), session_state)

    async def _ask_host_elicitation(
        self, server_name: str, message: str, requested_schema: Mapping[str, Any] | None, mode: str | None,
        url: str | None, elicitation_id: str | None,
    ) -> dict[str, Any]:
        """`structuredIO.handleElicitation(serverName, message, requestedSchema, signal, mode, url, id)`."""
        return dict(await self.sio.handle_elicitation(server_name, message, requested_schema, mode=mode, url=url,
                                                      elicitation_id=elicitation_id))

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

    def _on_hook_event(self, event: Mapping[str, Any]) -> None:
        """`registerHookEventHandler`'s mapping onto `system/hook_started` and `system/hook_response`."""
        common = {"hook_id": event["hookId"], "hook_name": event["hookName"], "hook_event": event["hookEvent"]}
        if event["type"] == "started":
            self.output.enqueue(self._system("hook_started", **common))
        else:
            self.output.enqueue(self._system(
                "hook_response", **common, output=event["output"], stdout=event["stdout"], stderr=event["stderr"],
                exit_code=event["exitCode"], outcome=event["outcome"]))

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
            for task in (reader, self._run_task, *self._background, *self._plugin_tasks):
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
        """The stream's end: detached handlers answered, background hooks finalized, then `output.done()`."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)
        await self.run_.ctx.hook_dispatcher.registry.finalize_pending()
        self._drain_sdk_events()
        self.output.done()

    # ── run() ────────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        self._session_state("running")
        if self.run_.mcp is not None:
            await self.run_.mcp.update_sdk()
        if self.plugin_install is not None:
            await self._await_plugin_install()
        try:
            while True:
                self._drain_sdk_events()
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
            self._session_state("idle")
            self._drain_sdk_events()
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
            self._drain_sdk_events()
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
        """`currentAgents`: the run's agents (plugin ones included), the `flagSettings` agents on top."""
        if self.run_.get_agent_definitions is not None:
            return self.run_.get_agent_definitions()
        from app.tools.agent_tool.load_agents_dir import get_agent_definitions_with_overrides

        definitions = get_agent_definitions_with_overrides(project_root_of(self.run_.ctx.workspace))
        definitions.update(self.run_.extra_agents)
        return definitions

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

    # ── plugins ──────────────────────────────────────────────────────────────

    def start_plugin_install(self) -> None:
        """
        `print.ts`'s background plugin installation for every headless run: the declared
        marketplaces reconciled and delisted plugins removed (`installPluginsForHeadless`), then,
        when anything changed, the plugin MCP servers applied (`applyPluginMcpDiff`). With
        `TDDAGENTS_SYNC_PLUGIN_INSTALL` the first turn waits for it (`pluginInstallPromise`).
        """
        if self.run_.settings_locations is None:
            return
        task = asyncio.ensure_future(self._install_plugins_and_apply_mcp())
        if is_env_truthy(os.environ.get(SYNC_PLUGIN_INSTALL_ENV)):
            self.plugin_install = task
        self._plugin_tasks.add(task)
        task.add_done_callback(self._plugin_tasks.discard)

    async def _install_plugins_and_apply_mcp(self) -> None:
        """`installPluginsAndApplyMcpInBackground`."""
        from app.utils.plugins.headless_plugin_install import (
            install_plugins_for_headless,
        )

        try:
            assert self.run_.settings_locations is not None
            if await install_plugins_for_headless(self.run_.settings_locations) and self.run_.mcp is not None:
                await self.run_.mcp.refresh_plugin_servers()
        except Exception as error:  # noqa: BLE001 - a failed background install never fails the run
            logger.error("Background plugin installation failed: %s", error)

    async def _await_plugin_install(self) -> None:
        """
        `run()`'s sync-install join: the install awaited (raced against
        `TDDAGENTS_SYNC_PLUGIN_INSTALL_TIMEOUT_MS` when set), then `refreshPluginState`.
        """
        install, self.plugin_install = self.plugin_install, None
        assert install is not None
        timeout_ms = _int_env(SYNC_PLUGIN_INSTALL_TIMEOUT_ENV)
        if timeout_ms > 0:
            done, _ = await asyncio.wait({install}, timeout=timeout_ms / 1000)
            if not done:
                logger.error("%s: plugin installation timed out after %dms", SYNC_PLUGIN_INSTALL_ENV, timeout_ms)
        else:
            await install
        if self.run_.refresh_plugins is not None:
            try:
                await self.run_.refresh_plugins(mcp=False)
            except Exception as error:  # noqa: BLE001 - the run goes on with the plugins it has
                logger.error("Plugin refresh after the sync install failed: %s", error)

    async def _control_reload_plugins(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        """
        `reload_plugins`: `refreshActivePlugins`, then the refreshed commands, agents (the
        `flagSettings` ones kept), enabled plugins, MCP server statuses and the error count.
        """
        from app.commands import format_description_with_source, get_commands
        from app.utils.plugins.plugin_loader import load_all_plugins_cache_only

        run = self.run_
        if run.refresh_plugins is None or run.settings_locations is None:
            self.respond_error(message, "Plugins are not available in this session")
            return
        result = await run.refresh_plugins()
        plugins: list[dict[str, Any]] = []
        try:
            loaded = await load_all_plugins_cache_only(run.settings_locations)
            plugins = [{"name": p.name, "path": p.path, "source": p.source} for p in loaded.enabled]
        except Exception as error:  # noqa: BLE001 - a read failure must not mask the reload, as upstream
            logger.error("reload_plugins: %s", error)
        commands = [c for c in get_commands(run.ctx.skill_registry) if not c.is_hidden]
        mcp = run.mcp
        self.respond(message, {
            "commands": [{"name": c.user_facing_name(), "description": format_description_with_source(c),
                          "argumentHint": c.argument_hint or ""} for c in commands],
            "agents": [_agent_info(name, agent) for name, agent in self.agent_definitions().items()],
            "plugins": plugins,
            "mcpServers": mcp.statuses() if mcp is not None else build_mcp_server_statuses(run.ctx.mcp_clients),
            "error_count": result.error_count,
        })

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
        if request.get("jsonSchema") and self.run_.json_schema is None:
            use_json_schema(self.run_, request["jsonSchema"])
        if request.get("sdkMcpServers") and self.run_.mcp is not None:
            # SDK MCP servers: placeholder configs, connected by the next run (`updateSdkMcp`).
            self.run_.mcp.add_sdk_servers([str(name) for name in request["sdkMcpServers"]])
        if request.get("hooks"):
            self.run_.ctx.hook_dispatcher.register_hook_callbacks(self._sdk_hook_matchers(request["hooks"]))
        if request.get("agents") and self.run_.add_agent_definitions is not None:
            from app.tools.agent_tool.load_agents_dir import parse_agents_from_json

            self.run_.add_agent_definitions(parse_agents_from_json(request["agents"]))
        agents = self.agent_definitions()
        commands = [c for c in get_commands(self.run_.ctx.skill_registry) if not c.is_hidden]
        self.respond(message, {
            "commands": [{"name": c.user_facing_name(), "description": format_description_with_source(c),
                          "argumentHint": c.argument_hint or ""} for c in commands],
            "agents": [_agent_info(name, a) for name, a in agents.items()],
            "output_style": DEFAULT_OUTPUT_STYLE,
            "available_output_styles": [DEFAULT_OUTPUT_STYLE],
            "models": [],
            "account": {"apiProvider": _api_provider(self.run_.config.model)},
            "pid": os.getpid(),
        })
        self.initialized = True
        if self.queue.peek(_is_main_thread) is not None:
            self._kick()

    def _sdk_hook_matchers(self, hooks: Mapping[str, Any]) -> dict[str, list[Any]]:
        """`initialize.hooks`: each matcher's callback ids as hooks the host answers (`hook_callback`)."""
        from app.schemas.hooks import HookMatcher

        registered: dict[str, list[Any]] = {}
        for event, matchers in hooks.items():
            registered[str(event)] = [
                HookMatcher(hooks=tuple(self.sio.create_hook_callback(str(callback_id), matcher.get("timeout"))
                                        for callback_id in matcher.get("hookCallbackIds") or ()),
                            matcher=matcher.get("matcher"))
                for matcher in matchers or ()
            ]
        return registered

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
        self.run_.ctx.thinking_config = self.options.thinking_config
        self.respond(message)

    async def _control_mcp_status(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        mcp = self.run_.mcp
        statuses = mcp.statuses() if mcp is not None else build_mcp_server_statuses(self.run_.ctx.mcp_clients)
        self.respond(message, {"mcpServers": statuses})

    async def _control_get_context_usage(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        """`collectContextData` over the conversation, the main loop model and the current tool pool."""
        from app.commands import collect_context_data
        from app.services.token_estimation import api_token_counter
        from app.utils.model.model import parse_user_specified_model

        run = self.run_
        ctx = run.ctx
        model = parse_user_specified_model(run.user_specified_model) if run.user_specified_model else run.config.model
        data = await collect_context_data(
            run.messages, main_loop_model=model, tools=ctx.refresh_tools() if ctx.refresh_tools else ctx.tools,
            agent_definitions=self.agent_definitions(), tool_context=ctx,
            custom_system_prompt=ctx.custom_system_prompt, append_system_prompt=ctx.append_system_prompt,
            count_tokens=api_token_counter(model))
        self.respond(message, dict(data))

    async def _control_rewind_files(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        """`handleRewindFiles`; a refused rewind answers its error unless it was a dry run."""
        from app.utils.file_history import handle_rewind_files

        dry_run = bool(request.get("dry_run", False))
        run = self.run_
        result = await handle_rewind_files(str(request.get("user_message_id")), run.store.get(), run.store.update,
                                           dry_run, workspace=run.ctx.workspace)
        if result.can_rewind or dry_run:
            self.respond(message, result.to_json())
        else:
            self.respond_error(message, result.error or "Unexpected error")

    # ── MCP ──────────────────────────────────────────────────────────────────

    def _answer_reconnect(self, message: Mapping[str, Any], result: Any) -> None:
        """`mcp_reconnect` / `mcp_toggle`'s answer: success when connected, else why not."""
        client = result.client
        kind = getattr(client, "type", None)
        if kind == "connected":
            self.respond(message)
        elif kind == "failed":
            self.respond_error(message, getattr(client, "error", None) or "Connection failed")
        else:
            self.respond_error(message, f"Server status: {kind}")

    async def _control_mcp_reconnect(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        mcp = self.run_.mcp
        name = str(request.get("serverName"))
        config = mcp.find_config(name) if mcp is not None else None
        if config is None:
            self.respond_error(message, f"Server not found: {name}")
            return
        self._answer_reconnect(message, await mcp.reconnect(name, config))

    async def _control_mcp_toggle(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        mcp = self.run_.mcp
        name = str(request.get("serverName"))
        config = mcp.find_config(name) if mcp is not None else None
        if config is None:
            self.respond_error(message, f"Server not found: {name}")
            return
        if not request.get("enabled"):
            await mcp.disable(name, config)
            self.respond(message)
            return
        self._answer_reconnect(message, await mcp.enable(name, config))

    async def _control_mcp_set_servers(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        """`applyMcpServerChanges`; the SDK servers connect after the answer (no deadlock on the host)."""
        mcp = self.run_.mcp
        if mcp is None:
            self.respond(message, {"added": [], "removed": [], "errors": {}})
            return
        response, sdk_changed = await mcp.set_servers(dict(request.get("servers") or {}))
        self.respond(message, response)
        if sdk_changed:
            self._spawn(mcp.update_sdk())

    async def _control_mcp_message(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        """A notification from an SDK server, for its client."""
        if self.run_.mcp is not None:
            await self.run_.mcp.deliver(str(request.get("server_name")), dict(request.get("message") or {}))
        self.respond(message)

    def _respond_outcome(self, message: Mapping[str, Any], outcome: Any) -> None:
        from app.cli.mcp_auth_control import McpControlError

        if isinstance(outcome, McpControlError):
            self.respond_error(message, outcome.error)
        else:
            self.respond(message, outcome.response)

    def _mcp_auth_dependencies(self) -> dict[str, Any]:
        from app.services.mcp.config import is_mcp_server_disabled

        mcp = self.run_.mcp
        cwd = mcp.cwd if mcp is not None else "."
        return {
            "find_config": lambda name: mcp.find_config(name) if mcp is not None else None,
            "reconnect": lambda name, config: _reconnect_only(
                name, config, cwd, mcp.connect_options if mcp is not None else {}),
            "apply_reconnect": lambda name, result: mcp.apply_reconnect(name, result) if mcp is not None else None,
            "is_server_disabled": lambda name: is_mcp_server_disabled(name, cwd),
        }

    async def _control_mcp_authenticate(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        from app.cli.mcp_auth_control import handle_mcp_authenticate

        self._respond_outcome(message, await handle_mcp_authenticate(
            str(request.get("serverName")), flows=self.oauth_flows, **self._mcp_auth_dependencies()))

    async def _control_mcp_oauth_callback_url(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        from app.cli.mcp_auth_control import handle_mcp_oauth_callback_url

        self._respond_outcome(message, await handle_mcp_oauth_callback_url(
            str(request.get("serverName")), str(request.get("callbackUrl")), flows=self.oauth_flows))

    async def _control_mcp_clear_auth(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        from app.cli.mcp_auth_control import handle_mcp_clear_auth

        dependencies = self._mcp_auth_dependencies()
        dependencies.pop("is_server_disabled")
        self._respond_outcome(message, await handle_mcp_clear_auth(str(request.get("serverName")), **dependencies))

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

    async def _control_generate_session_title(
        self, message: Mapping[str, Any], request: Mapping[str, Any]
    ) -> None:
        """Fire-and-forget, as upstream: the small fast model's call never blocks the stdin loop."""
        from app.utils.session_title import generate_session_title

        run = self.run_

        async def answer() -> None:
            title = await generate_session_title(str(request.get("description") or ""),
                                                 run.deps.query_haiku(run.ctx))
            if title and request.get("persist"):
                try:
                    run.transcript.save_ai_generated_title(title)
                except OSError as error:
                    logger.error("saveAiGeneratedTitle failed: %s", error)
            self.respond(message, {"title": title})

        self._spawn(answer())

    async def _control_side_question(self, message: Mapping[str, Any], request: Mapping[str, Any]) -> None:
        """Fire-and-forget, as upstream: one forked model call over the conversation, which stays as it was."""
        from app.query.state import initial_loop_state
        from app.tool import CancelToken
        from app.utils.side_question import run_side_question

        run = self.run_

        async def answer() -> None:
            try:
                ctx = replace(run.ctx, messages=tuple(run.messages), cancel=CancelToken())
                state = initial_loop_state(tuple(run.messages), ctx)
                response = await run_side_question(str(request.get("question") or ""), state, run.config,
                                                   run.deps.call_model)
            except Exception as error:  # noqa: BLE001 - upstream answers the request with the error
                self.respond_error(message, str(error))
                return
            self.respond(message, {"response": response})

        self._spawn(answer())

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


async def _reconnect_only(name: str, config: Any, cwd: str, options: Mapping[str, Any]) -> Any:
    """`reconnectMcpServerImpl` bound to the session's cwd and connection options (the caller applies it)."""
    from app.services.mcp.mcp_connection_manager import reconnect_mcp_server_impl

    return await reconnect_mcp_server_impl(name, config, cwd=cwd, **options)


def _status_config(config: Any) -> dict[str, Any] | None:
    """`buildMcpServerStatuses`' `config`: the fields the SDK shows for each transport."""
    kind = getattr(config, "type", None)
    if kind in ("sse", "http"):
        oauth = config.oauth
        return _defined({"type": kind, "url": config.url, "headers": dict(config.headers) if config.headers else None,
                         "oauth": _defined(vars(oauth)) if oauth is not None else None})
    if kind == "claudeai-proxy":
        return _defined({"type": kind, "url": config.url, "id": getattr(config, "id", None)})
    if kind in ("stdio", None):
        return _defined({"type": "stdio", "command": config.command, "args": list(config.args)})
    return None


def _defined(values: Mapping[str, Any]) -> dict[str, Any]:
    """`JSON.stringify` drops `undefined`."""
    return {key: value for key, value in values.items() if value is not None}


def _server_info(info: Any) -> dict[str, Any] | None:
    if info is None:
        return None
    if hasattr(info, "model_dump"):
        dumped = info.model_dump(by_alias=True, exclude_none=True)
        return {key: dumped[key] for key in ("name", "version") if key in dumped}
    return dict(info) if isinstance(info, Mapping) else None


#: `CLAUDE_CODE_SYNC_PLUGIN_INSTALL_TIMEOUT_MS`, this build's name.
SYNC_PLUGIN_INSTALL_TIMEOUT_ENV = "TDDAGENTS_SYNC_PLUGIN_INSTALL_TIMEOUT_MS"


def _int_env(name: str) -> int:
    """`parseInt(process.env[name] || '', 10)`, `0` when unset or not a number."""
    try:
        return int(os.environ.get(name, "").strip() or 0)
    except ValueError:
        return 0


def _agent_info(name: str, agent: Any) -> dict[str, Any]:
    """`AgentInfo`: `{name, description, model?}` (`inherit` is no model)."""
    model = getattr(agent, "model", None)
    return {"name": name, "description": agent.description,
            **({"model": model} if model not in (None, "inherit") else {})}


def build_mcp_server_statuses(clients: Sequence[Any], tools: Sequence[Any] = ()) -> list[dict[str, Any]]:
    """
    `buildMcpServerStatuses`: each connection's name, status, server info, error, config, scope and
    (connected only) tools, read from the merged MCP tool list by the server's prefix
    (`filterToolsByServer`). The caller orders the connections and drops duplicate names.
    """
    from app.services.mcp.normalization import normalize_name_for_mcp

    statuses = []
    for client in clients:
        connected = getattr(client, "type", None) == "connected"
        server_tools = None
        if connected:
            prefix = f"mcp__{normalize_name_for_mcp(client.name)}__"
            server_tools = [
                {"name": getattr(tool, "mcp_tool_name", None) or tool.name,
                 "annotations": _defined({"readOnly": _hint(tool, "is_read_only"),
                                          "destructive": _hint(tool, "is_destructive")})}
                for tool in tools if tool.name.startswith(prefix)
            ]
        config = getattr(client, "config", None)
        statuses.append(_defined({
            "name": client.name,
            "status": _mcp_status(client),
            "serverInfo": _server_info(getattr(client, "server_info", None)) if connected else None,
            "error": getattr(client, "error", None) if getattr(client, "type", None) == "failed" else None,
            "config": _status_config(config) if config is not None else None,
            "scope": getattr(config, "scope", None),
            "tools": server_tools,
        }))
    return statuses


def _hint(tool: Any, method: str) -> bool | None:
    """`tool.isReadOnly({}) || undefined`."""
    check = getattr(tool, method, None)
    return (bool(check({})) or None) if callable(check) else None


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
    "NoConversationFound",
    "PERMISSION_PROMPT_ABORTED",
    "PermissionPromptToolError",
    "create_can_use_tool_with_permission_prompt",
    "get_permission_prompt_tool_can_use_tool",
    "fork_thread",
    "most_recent_thread",
    "run_headless",
    "thread_transcript",
    "use_json_schema",
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
