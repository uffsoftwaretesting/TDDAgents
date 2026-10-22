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
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.loop.agents.memory import AgentMemoryStore
from app.loop.config import RunConfig, build_run_config
from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.coordinator import (
    apply_coordinator_tool_filter,
    get_coordinator_system_prompt,
    get_coordinator_user_context,
    is_coordinator_mode,
)
from app.loop.cost import CostTracker
from app.loop.deps import LoopDeps
from app.loop.events import TransitionEventRecorder, events_path
from app.loop.hooks.execute import HookDispatcher
from app.loop.permissions.settings import (
    SettingsLocations,
    build_tool_permission_context,
)
from app.loop.permissions.types import ToolPermissionContext
from app.loop.query_engine import QueryEngine
from app.loop.state import initial_loop_state
from app.loop.storage.session_storage import TranscriptStore
from app.loop.tools.base import Tool
from app.loop.tools.pool import assemble_tool_pool
from app.loop.transitions import Terminal
from app.session.state import PlanItemResult, SessionState
from app.workspace.local import LocalWorkspace

logger = logging.getLogger("loop_runner")


def build_builtin_tools() -> list[Tool]:
    """
    The Phase 0 roster: the file primitives, Bash and RunTests.

    No per-run template vars, as in claude-code: a tool's prompt is static, and what varies per
    run (the workspace, the phase) reaches the tool through `ToolContext` at call time.
    """
    from app.loop.tools.bash import build_bash_tool
    from app.loop.tools.fs import (
        build_edit_tool,
        build_glob_tool,
        build_grep_tool,
        build_read_file_tool,
        build_write_file_tool,
    )
    from app.loop.tools.run_tests import build_run_tests_tool
    from app.loop.tools.todo import build_todo_write_tool

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


def main_thread_tools(config: RunConfig, deps: LoopDeps, project_root: Path, home: Path | None) -> list[Tool]:
    """
    The main thread's roster: the built-ins plus the Agent tool, as upstream's main thread
    always has it. Workers draw from the built-ins alone (upstream's `assembleToolPool` for
    the worker), and `resolveAgentTools` keeps Agent out of every worker. In coordinator mode
    the main thread keeps only the orchestration tools (`applyCoordinatorToolFilter`).
    """
    from app.loop.agents.loader import get_agent_definitions_with_overrides
    from app.loop.agents.tool import build_agent_tool

    built_ins = build_builtin_tools()
    coordinator = is_coordinator_mode(config)
    definitions = get_agent_definitions_with_overrides(project_dir=project_root, user_home=home)
    agent_tool = build_agent_tool(
        definitions=definitions, worker_tools=built_ins, config=config, deps=deps, is_coordinator=coordinator
    )
    tools: list[Tool] = [*built_ins, agent_tool]
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
    from app.loop.permissions.settings import MANAGED_DIR

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
    from app.loop.permissions.settings import MANAGED_DIR

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
) -> PlanItemResult:
    """
    Run one sub-requirement through the loop and report what the ledger observed.

    `project_root` is where the project and local settings live (the repository, upstream's
    original cwd); the workspace is the working directory the rules anchor to.
    `home` and `managed_dir` relocate the user and policy scopes (tests use them).

    Hooks follow upstream's headless session: SessionStart (`startup`) and UserPromptSubmit
    (the sub-requirement) before the first model call, SessionEnd (`other`) after the last,
    then any background hook still running is killed. A UserPromptSubmit hook that blocks or
    says `continue: false` stops the run before the model is called: `hook_stopped`, failed.
    """
    run_id = f"loop-{uuid.uuid4().hex[:8]}"
    ws = workspace if workspace is not None else default_workspace(state)
    if deps is None:
        from app.loop.factory import get_production_deps

        deps = get_production_deps()
    config = build_run_config(run_id, postgres_checkpointing=False)
    perm = permission_context or run_permission_context(config, ws, project_root, home, managed_dir)

    store = AppStateStore(state=AppState(tool_permission_context=perm))
    ledger = store.get().phase_ledger
    root_dir = Path(project_root) if project_root is not None else Path.cwd()
    built_ins = main_thread_tools(config, deps, root_dir, home)
    pool_cache: dict[str, Any] = {
        "phase": ledger.phase,
        "tools": assemble_tool_pool(built_ins, phase_ledger=ledger),
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
            pool_cache["tools"] = assemble_tool_pool(built_ins, phase_ledger=current)
        tools_now: tuple[Tool, ...] = pool_cache["tools"]
        return tools_now

    run_dir = getattr(ws, "run_dir", None)
    transcript_dir = Path(run_dir) if run_dir is not None else None
    cwd = str(getattr(ws, "root", None) or root_dir)
    transcript = TranscriptStore(run_id, cwd)

    if hook_dispatcher is None:
        from app.loop.hooks.runtime import build_hook_dispatcher

        hook_dispatcher = build_hook_dispatcher(
            run_settings_locations(config, root_dir, home, managed_dir),
            session_id=run_id,
            cwd=cwd,
            project_dir=str(root_dir),
            model=config.model,
            transcript_path=str(transcript.path),
        )

    memory_store, memory_root = run_agent_memory_store(transcript_dir)
    initial_message = HumanMessage(content=sub_req)
    ctx = tool_context_for(
        store,
        messages=(initial_message,),
        tools=refresh_tools(),
        workspace=ws,
        hook_dispatcher=hook_dispatcher,
        refresh_tools=refresh_tools,
        run_id=run_id,
        agent_memory_store=memory_store,
        cost_tracker=CostTracker(config.model),
        transcript_path=str(transcript.path),
        transcript_store=transcript,
        **coordinator_context(config),
    )
    try:
        return await _run_with_hooks(
            sub_req, idx, run_id, initial_message, ctx, store, config, deps, transcript, events_path(run_id, cwd)
        )
    finally:
        from app.loop.hooks.execute import execute_session_end_hooks

        await hook_dispatcher.drain_background()
        await execute_session_end_hooks(hook_dispatcher, "other")
        await hook_dispatcher.registry.finalize_pending()
        memory_store.discard_run_memory(run_id)
        if memory_root is not None:
            shutil.rmtree(memory_root, ignore_errors=True)


def _both(first: Any, second: Any) -> Any:
    """The run's event recorder, then whatever sink the caller's deps already had."""

    def emit(event: Any) -> None:
        first(event)
        second(event)

    return emit


async def _run_with_hooks(
    sub_req: str,
    idx: int,
    run_id: str,
    initial_message: HumanMessage,
    ctx: Any,
    store: AppStateStore,
    config: RunConfig,
    deps: LoopDeps,
    transcript: TranscriptStore,
    events_file: Path,
) -> PlanItemResult:
    """SessionStart and UserPromptSubmit, then the loop itself."""
    from app.loop.hooks.session import (
        process_session_start_hooks,
        process_user_prompt_submit_hooks,
    )

    session = await process_session_start_hooks(ctx.hook_dispatcher, "startup", model=config.model)
    mode = str(store.get().tool_permission_context.mode)
    prompt = await process_user_prompt_submit_hooks(ctx, sub_req, mode)
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
    messages = (*session.messages, *first_turn, initial_message, *prompt.messages)
    # `processUserInput`: the user-input attachments (the initial TDD state among them),
    # persisted into the history the loop starts from.
    from app.loop.attachments import get_attachment_messages

    attachments = await get_attachment_messages(ctx, messages, user_input=sub_req)
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
    "main_thread_tools",
    "run_agent_memory_store",
    "run_permission_context",
    "run_settings_locations",
    "session_workspace",
    "tdd_loop_runner",
]
