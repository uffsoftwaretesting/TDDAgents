"""
Driving a session from the entry point (Phase 9): build the shell, start or continue a
thread, and answer the analyst's interrupts.

`print.ts` -> `runHeadless` is the model for the headless path: no human, so nothing waits
on input. Here that is the shell's `headless` flag — the analyst-review node approves a
checklist and fails the session on a clarifying question instead of interrupting — so a
headless drive never answers anything. Interactively, each pending interrupt is handed to
the caller's `answer` callback; `None` from it leaves the thread paused at the checkpoint,
resumable later with the same thread id.

A thread with a checkpoint is never restarted: a finished one is reported as it stands, an
unfinished one continues from its last checkpoint (`continue_session`). That is how a
session survives a restart — the plan item that was running when the process died runs
again and resumes its own transcript (`app/cli/print.py::load_initial_messages`).
"""

from __future__ import annotations

import functools
import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.config.config import Config
from app.session.checkpointer import build_checkpointer, is_persistent_checkpointer
from app.session.shell import SessionShell
from app.session.state import SessionStatus

logger = logging.getLogger(__name__)

#: `--checkpoint memory`: an in-memory checkpointer, no cross-restart resume.
MEMORY_CHECKPOINT = "memory"


@dataclass(frozen=True, slots=True)
class SessionOptions:
    """The entry point's flags that shape a session (upstream's startup flags among them)."""

    headless: bool = False
    #: A checkpointer URI, `MEMORY_CHECKPOINT`, or `None` for `Config.POSTGRES_URL`.
    checkpoint: str | None = None
    permission_mode: str | None = None
    settings_path: str | None = None
    dangerously_skip_permissions: bool = False


@dataclass(frozen=True, slots=True)
class SessionRun:
    """A session as the drive left it: its state, and the interrupt it is paused at, if any."""

    values: dict[str, Any]
    paused_at: dict[str, Any] | None = None

    @property
    def completed(self) -> bool:
        return self.values.get("status") == SessionStatus.COMPLETED


def derive_thread_id(initial_input: str) -> str:
    """A thread id derived from the initial request alone, so the same request finds its session."""
    return "tdd-" + hashlib.sha1(f"session::{initial_input}".encode()).hexdigest()[:16]


def fresh_thread_id() -> str:
    return "tdd-" + uuid.uuid4().hex[:16]


def checkpoint_uri(option: str | None) -> str | None:
    """The URI `build_checkpointer` receives: the option, or `Config.POSTGRES_URL` by default."""
    return Config.POSTGRES_URL if option is None else option


def build_session_shell(
    thread_id: str,
    options: SessionOptions,
    *,
    workspace_factory: Callable[[str], Any] | None = None,
    loop_runner: Callable[..., Any] | None = None,
    analyzer_fn: Callable[[str, str], dict[str, Any]] | None = None,
    engineer_fn: Callable[[str, str], str] | None = None,
    planner_fn: Callable[[str], list[str]] | None = None,
) -> tuple[SessionShell, Any]:
    """
    The session's shell and its shared workspace. Every plan item runs through
    `tdd_loop_runner` on that workspace, told whether the checkpointer really persists and
    given the run's permission flags.
    """
    from app.cli.print import session_workspace, tdd_loop_runner

    checkpointer = build_checkpointer(checkpoint_uri(options.checkpoint))
    persistent = is_persistent_checkpointer(checkpointer)
    if not persistent:
        logger.warning("Session %s checkpoints in memory: it cannot be resumed after this process ends.", thread_id)
    workspace = (workspace_factory or session_workspace)(thread_id)
    runner = functools.partial(
        loop_runner or tdd_loop_runner,
        workspace=workspace,
        postgres_checkpointing=persistent,
        permission_mode=options.permission_mode,
        settings_path=options.settings_path,
        dangerously_skip_permissions=options.dangerously_skip_permissions,
    )
    shell = SessionShell(
        checkpointer=checkpointer,
        analyzer_fn=analyzer_fn,
        engineer_fn=engineer_fn,
        planner_fn=planner_fn,
        loop_runner=runner,
        headless=options.headless,
    )
    return shell, workspace


def drive_session(
    shell: SessionShell,
    thread_id: str,
    initial_input: str | None,
    answer: Callable[[dict[str, Any]], str | None] | None = None,
) -> SessionRun:
    """
    Start `thread_id` from `initial_input`, or continue it from its checkpoint, then answer
    its interrupts until it finishes or `answer` returns `None`. Without `answer` (headless)
    a pending interrupt is not answered: the thread continues and the shell decides.
    """
    if shell.is_finished(thread_id):
        logger.info("Session %s already finished; reporting its checkpoint.", thread_id)
    elif shell.has_session(thread_id):
        if answer is None or not shell.pending_interrupts(thread_id):
            logger.info("Continuing session %s from its last checkpoint.", thread_id)
            shell.continue_session(thread_id)
    elif initial_input is None:
        raise ValueError(f"no session '{thread_id}' to continue, and no initial request to start one")
    else:
        shell.run(initial_input, thread_id)

    while answer is not None:
        pending = shell.pending_interrupts(thread_id)
        if not pending:
            break
        reply = answer(pending[0])
        if reply is None:
            return SessionRun(values=dict(shell.get_state(thread_id).values), paused_at=pending[0])
        shell.resume(thread_id, reply)
    return SessionRun(values=dict(shell.get_state(thread_id).values))


def failed_requirements(values: dict[str, Any]) -> list[dict[str, Any]]:
    """The failed plan items in the shape the pass-rate report reads: requirement and reason."""
    return [
        {
            "requirement": item.get("sub_requirement", ""),
            "reason": item.get("error_message") or item.get("terminal_reason"),
        }
        for item in values.get("subreq_results") or []
        if item.get("status") == "failed"
    ]


__all__ = [
    "MEMORY_CHECKPOINT",
    "SessionOptions",
    "SessionRun",
    "build_session_shell",
    "checkpoint_uri",
    "derive_thread_id",
    "drive_session",
    "failed_requirements",
    "fresh_thread_id",
]
