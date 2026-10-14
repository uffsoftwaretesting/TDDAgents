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
* **Transcript.** Written beside the workspace, never inside it, so agents cannot read or
  edit their own record and it never reaches the exported artifact.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.loop.config import RunConfig, build_run_config
from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.deps import LoopDeps
from app.loop.engine import drain, run_loop
from app.loop.permissions.settings import build_tool_permission_context
from app.loop.permissions.types import ToolPermissionContext
from app.loop.state import initial_loop_state
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
) -> PlanItemResult:
    """
    Run one sub-requirement through the loop and report what the ledger observed.

    `project_root` is where the project and local settings live (the repository, upstream's
    original cwd); the workspace is the working directory the rules anchor to.
    `home` and `managed_dir` relocate the user and policy scopes (tests use them).
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
    built_ins = build_builtin_tools()
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

    initial_message = HumanMessage(content=sub_req)
    ctx = tool_context_for(
        store,
        messages=(initial_message,),
        tools=refresh_tools(),
        workspace=ws,
        refresh_tools=refresh_tools,
        run_id=run_id,
    )
    loop_state = initial_loop_state((initial_message,), ctx)

    from app.loop.transcript import TranscriptLogger, with_transcript_logger

    run_dir = getattr(ws, "run_dir", None)
    transcript_dir = Path(run_dir) if run_dir is not None else None
    transcript = TranscriptLogger(run_id=run_id, base_dir=transcript_dir)

    logger.info("Launching TDD loop %s for sub-requirement %d: %s", run_id, idx, sub_req)
    try:
        terminal = await drain(with_transcript_logger(run_loop(loop_state, config, deps), transcript))
        reason = terminal.reason
    except Exception:
        logger.exception("Loop %s crashed", run_id)
        reason = Terminal.MODEL_ERROR

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
    "default_workspace",
    "run_permission_context",
    "session_workspace",
    "tdd_loop_runner",
]
