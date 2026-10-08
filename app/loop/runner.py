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
* **Permissions.** The run is headless (`should_avoid_permission_prompts`): an `ask` nobody
  can answer is a deny. Settings-loaded rules are wired in Phase 3.
* **Transcript.** Written beside the workspace, never inside it, so agents cannot read or
  edit their own record and it never reaches the exported artifact.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.loop.config import build_run_config
from app.loop.context import AppStateStore, tool_context_for
from app.loop.deps import LoopDeps
from app.loop.engine import drain, run_loop
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

    return [
        build_read_file_tool(),
        build_write_file_tool(),
        build_edit_tool(),
        build_glob_tool(),
        build_grep_tool(),
        build_bash_tool(),
        build_run_tests_tool(),
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
) -> PlanItemResult:
    """Run one sub-requirement through the loop and report what the ledger observed."""
    run_id = f"loop-{uuid.uuid4().hex[:8]}"
    ws = workspace if workspace is not None else default_workspace(state)
    if deps is None:
        from app.loop.factory import get_production_deps

        deps = get_production_deps()
    perm = permission_context or ToolPermissionContext(should_avoid_permission_prompts=True)

    store = AppStateStore()
    ledger = store.get().phase_ledger
    tools = assemble_tool_pool(build_builtin_tools(), phase_ledger=ledger)

    initial_message = HumanMessage(content=sub_req)
    ctx = tool_context_for(
        store,
        messages=(initial_message,),
        tools=tools,
        permission_context=perm,
        workspace=ws,
    )
    loop_state = initial_loop_state((initial_message,), ctx)
    config = build_run_config(run_id, postgres_checkpointing=False)

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


__all__ = ["build_builtin_tools", "default_workspace", "session_workspace", "tdd_loop_runner"]
