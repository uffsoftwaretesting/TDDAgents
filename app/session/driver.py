"""
Driving a session from the entry point: pick its project, then run its one conversation, which
opens in plan mode (the Analyst, `app/tools/exit_plan_mode_tool/`) and develops the approved plan.

`print.ts` -> `runHeadless` is the model for the headless path (`develop_headless`): nobody is
asked anything, so the plan's approval is denied and a headless session plans without developing,
as upstream's `-p`. Interactively (`develop_interactive`) the conversation is the REPL
(`app/screens/repl.py`), where the user answers the Analyst's questions, approves the plan and
talks to the agents while they work.

A session is resumed as upstream resumes one: its transcript, its TDD plan sidecar and its plan
file (`app/cli/print.py`), found again through the thread id. A session never restarts.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.cli.print import DevelopmentOutcome

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SessionOptions:
    """The entry point's flags that shape a session (upstream's startup flags among them)."""

    headless: bool = False
    permission_mode: str | None = None
    settings_path: str | None = None
    dangerously_skip_permissions: bool = False


def derive_thread_id(initial_input: str) -> str:
    """A thread id derived from the initial request alone, so the same request finds its session."""
    return "tdd-" + hashlib.sha1(f"session::{initial_input}".encode()).hexdigest()[:16]


def fresh_thread_id() -> str:
    return "tdd-" + uuid.uuid4().hex[:16]


def session_exists(thread_id: str, workspace: Any) -> bool:
    """Whether `thread_id` has a conversation to resume: its transcript is on disk."""
    from app.cli.print import project_root_of, session_run_id
    from app.utils.session_storage import TranscriptStore

    cwd = str(getattr(workspace, "root", None) or project_root_of(workspace, None))
    return TranscriptStore(session_run_id(thread_id), cwd).path.exists()


def development_run_options(options: SessionOptions) -> dict[str, Any]:
    """
    What the run is told: upstream's startup flags (`--permission-mode`, `--settings`,
    `--dangerously-skip-permissions`).
    """
    return {
        "permission_mode": options.permission_mode,
        "settings_path": options.settings_path,
        "dangerously_skip_permissions": options.dangerously_skip_permissions,
    }


def develop_headless(
    thread_id: str, request: str | None, workspace: Any, options: SessionOptions,
    *, prompt: str | None = None, **run_options: Any,
) -> DevelopmentOutcome:
    """
    The session with nobody to ask (`run_headless`), to its end. `prompt` continues a resumed
    conversation (the user's answer to the question it stopped on).
    """
    from app.cli.print import run_headless
    from app.utils.run_sync import run_sync

    return run_sync(run_headless(thread_id, request, workspace=workspace, prompt=prompt,
                                 **{**development_run_options(options), **run_options}))


def develop_interactive(
    thread_id: str, request: str | None, workspace: Any, options: SessionOptions,
    *, io_factory: Callable[[Callable[[], None]], Any] | None = None, prompt: str | None = None,
    **run_options: Any,
) -> DevelopmentOutcome:
    """
    The session at the terminal (`app/screens/repl.py`), until the user leaves. The Analyst's
    questions, the plan's approval, permission questions and the model's `AskUserQuestion` are
    answered on the same terminal. `prompt` continues a resumed conversation.
    """
    from app.utils.run_sync import run_sync

    return run_sync(_develop_interactive(thread_id, request, workspace, options, io_factory, prompt, run_options))


async def _develop_interactive(
    thread_id: str, request: str | None, workspace: Any, options: SessionOptions,
    io_factory: Callable[[Callable[[], None]], Any] | None, prompt: str | None, run_options: dict[str, Any],
) -> DevelopmentOutcome:
    from app.cli.print import open_session_run, outcome_of, session_run_id
    from app.hooks.use_can_use_tool import (
        PermissionAnswer,
        PermissionPrompt,
        build_interactive_can_use_tool,
    )
    from app.screens.repl import Repl
    from app.tdd.plan import TddPlan

    if io_factory is None:
        from app.screens.terminal_io import TerminalIO

        io_factory = TerminalIO
    holder: dict[str, Repl] = {}

    async def ask(permission: PermissionPrompt) -> PermissionAnswer:
        return await holder["repl"].ask_permission(permission)

    async def ask_questions(questions: tuple[dict[str, Any], ...]) -> dict[str, str] | None:
        return await holder["repl"].ask_questions(questions)

    opts = {**development_run_options(options), **run_options}
    async with open_session_run(run_id=session_run_id(thread_id), plan=TddPlan(), workspace=workspace,
                                planning=True, can_use_tool=build_interactive_can_use_tool(ask, ask_questions),
                                **opts) as run:
        io = io_factory(lambda: holder["repl"].interrupt())
        repl = Repl(run, io)
        holder["repl"] = repl
        with io:
            terminal = await repl.run_session(request, prompt)
        return outcome_of(run, terminal)


__all__ = [
    "SessionOptions",
    "derive_thread_id",
    "develop_headless",
    "develop_interactive",
    "development_run_options",
    "fresh_thread_id",
    "session_exists",
]
