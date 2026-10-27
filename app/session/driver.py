"""
Driving a session from the entry point (Phases 9 and 11): build the shell, start or continue
a thread, answer the analyst's interrupts, and hand the plan to development.

`print.ts` -> `runHeadless` is the model for the headless path: no human, so nothing waits
on input. Here that is the shell's `headless` flag — the analyst-review node approves a
checklist and fails the session on a clarifying question instead of interrupting — so a
headless drive never answers anything. Interactively, each pending interrupt is handed to
the caller's `answer` callback; `None` from it leaves the thread paused at the checkpoint,
resumable later with the same thread id.

A thread with a checkpoint is never restarted: a finished one is reported as it stands, an
unfinished one continues from its last checkpoint (`continue_session`). A planned thread goes
on to development, whose conversation and plan are resumed from their own files
(`app/cli/print.py`), so a restarted session continues where it stopped.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.config import Config
from app.session.checkpointer import build_checkpointer, is_persistent_checkpointer
from app.session.shell import SessionShell
from app.session.state import SessionStatus

if TYPE_CHECKING:
    from app.cli.print import DevelopmentOutcome

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
    def planned(self) -> bool:
        """The shell has produced the plan: development can start (or continue)."""
        return self.values.get("status") == SessionStatus.PLANNED


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
    analyzer_fn: Callable[[str, str], dict[str, Any]] | None = None,
    engineer_fn: Callable[[str, str], str] | None = None,
    planner_fn: Callable[[str, Sequence[str]], list[str]] | None = None,
) -> tuple[SessionShell, Any]:
    """The session's shell and its shared workspace, where development will run."""
    from app.cli.print import session_workspace

    checkpointer = build_checkpointer(checkpoint_uri(options.checkpoint))
    if not is_persistent_checkpointer(checkpointer):
        logger.warning("Session %s checkpoints in memory: it cannot be resumed after this process ends.", thread_id)
    workspace = (workspace_factory or session_workspace)(thread_id)
    shell = SessionShell(
        checkpointer=checkpointer,
        analyzer_fn=analyzer_fn,
        engineer_fn=engineer_fn,
        planner_fn=planner_fn,
        headless=options.headless,
    )
    return shell, workspace


def development_run_options(shell: SessionShell, options: SessionOptions) -> dict[str, Any]:
    """
    What the development run is told: whether the checkpointer really persists, and the
    startup flags (`--permission-mode`, `--settings`, `--dangerously-skip-permissions`).
    """
    return {
        "postgres_checkpointing": is_persistent_checkpointer(shell.checkpointer),
        "permission_mode": options.permission_mode,
        "settings_path": options.settings_path,
        "dangerously_skip_permissions": options.dangerously_skip_permissions,
    }


def develop_headless(
    shell: SessionShell, thread_id: str, values: dict[str, Any], workspace: Any, options: SessionOptions,
    **run_options: Any,
) -> DevelopmentOutcome:
    """A planned session's development with nobody to ask (`run_headless`), to its end."""
    from app.cli.print import run_headless
    from app.session.llm import run_sync

    return run_sync(run_headless(
        thread_id, str(values.get("specification") or ""), list(values.get("plan") or []),
        workspace=workspace, **{**development_run_options(shell, options), **run_options},
    ))


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

    return answer_interrupts(shell, thread_id, answer)


#: The answers that leave the analyst's question (the session stays paused at its checkpoint).
EXIT_COMMANDS = ("/exit", "/quit")


def analyst_review_text(payload: dict[str, Any]) -> str:
    """The analyst's answer and the tip on how to reply to it."""
    lines = [f"\n🤖 [Requirements Analyst]:\n{payload.get('prompt', '')}\n"]
    if payload.get("has_checklist"):
        lines += [
            "💡 TIP: The Analyst has generated the Final Checklist.",
            "   - Type '/yes' to proceed to Engineering.",
            "   - Or type the changes you'd like to make (e.g. 'Missing validation X').",
        ]
    else:
        lines += ["💡 TIP: The Analyst needs more details.", "   - Answer the question to help define the scope."]
    return "\n".join(lines)


def answer_interrupts(
    shell: SessionShell, thread_id: str, answer: Callable[[dict[str, Any]], str | None] | None
) -> SessionRun:
    """Answer `thread_id`'s interrupts until none is pending or `answer` returns `None`."""
    while answer is not None:
        pending = shell.pending_interrupts(thread_id)
        if not pending:
            break
        reply = answer(pending[0])
        if reply is None:
            return SessionRun(values=dict(shell.get_state(thread_id).values), paused_at=pending[0])
        shell.resume(thread_id, reply)
    return SessionRun(values=dict(shell.get_state(thread_id).values))


def rescope_session(
    shell: SessionShell,
    thread_id: str,
    request: str,
    completed_items: Sequence[str],
    answer: Callable[[dict[str, Any]], str | None] | None,
) -> SessionRun:
    """`/requirements`: the analyst, the engineer and the planner again, with `request`."""
    shell.rescope(thread_id, request, completed_items)
    return answer_interrupts(shell, thread_id, answer)


def develop_interactive(
    shell: SessionShell, thread_id: str, values: dict[str, Any], workspace: Any, options: SessionOptions,
    *, io_factory: Callable[[Callable[[], None]], Any] | None = None, **run_options: Any,
) -> DevelopmentOutcome:
    """
    A planned session's development at the terminal (`app/screens/repl.py`), until the user
    leaves. Permission questions and `/requirements` (the analyst's questions included) are
    answered on the same terminal.
    """
    from app.session.llm import run_sync

    return run_sync(_develop_interactive(shell, thread_id, values, workspace, options, io_factory, run_options))


async def _develop_interactive(
    shell: SessionShell, thread_id: str, values: dict[str, Any], workspace: Any, options: SessionOptions,
    io_factory: Callable[[Callable[[], None]], Any] | None, run_options: dict[str, Any],
) -> DevelopmentOutcome:
    import asyncio

    from app.cli.print import DevelopmentOutcome, open_session_run, session_run_id
    from app.hooks.use_can_use_tool import (
        PermissionAnswer,
        PermissionPrompt,
        build_interactive_can_use_tool,
    )
    from app.screens.repl import Repl
    from app.session.prompts import development_resume_prompt, development_start_prompt
    from app.tdd.plan import ItemStatus, TddPlan

    if io_factory is None:
        from app.screens.terminal_io import TerminalIO

        io_factory = TerminalIO
    holder: dict[str, Repl] = {}

    async def ask(prompt: PermissionPrompt) -> PermissionAnswer:
        return await holder["repl"].ask_permission(prompt)

    specification = str(values.get("specification") or "")
    plan = TddPlan.from_texts(list(values.get("plan") or []))
    opts = {**development_run_options(shell, options), **run_options}
    async with open_session_run(run_id=session_run_id(thread_id), plan=plan, workspace=workspace,
                                can_use_tool=build_interactive_can_use_tool(ask), **opts) as run:
        io = io_factory(lambda: holder["repl"].interrupt())
        loop = asyncio.get_running_loop()

        def answer(payload: dict[str, Any]) -> str | None:
            io.write(analyst_review_text(payload))
            line = asyncio.run_coroutine_threadsafe(io.read_line("👤 "), loop).result()
            if line is None or line.strip().lower() in EXIT_COMMANDS:
                return None
            return str(line)

        async def rescope(request: str) -> tuple[str, list[str]] | None:
            done = [item.text for item in run.store.get().tdd_plan.items if item.status == ItemStatus.DONE]
            result = await asyncio.to_thread(rescope_session, shell, thread_id, request, done, answer)
            if not result.planned:
                return None
            return str(result.values.get("specification") or ""), list(result.values.get("plan") or [])

        repl = Repl(run, io, specification=specification, rescope=rescope)
        holder["repl"] = repl
        current = run.store.get().tdd_plan
        with io:
            terminal = await repl.run_session(
                development_start_prompt(specification, current), development_resume_prompt(current)
            )
        return DevelopmentOutcome(terminal=terminal, plan=run.store.get().tdd_plan)


__all__ = [
    "MEMORY_CHECKPOINT",
    "SessionOptions",
    "SessionRun",
    "build_session_shell",
    "checkpoint_uri",
    "answer_interrupts",
    "EXIT_COMMANDS",
    "analyst_review_text",
    "derive_thread_id",
    "develop_headless",
    "develop_interactive",
    "development_run_options",
    "drive_session",
    "fresh_thread_id",
    "rescope_session",
]
