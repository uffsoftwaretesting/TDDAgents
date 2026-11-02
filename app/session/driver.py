"""
Driving a session from the entry point: pick its project, then run its one conversation, which
opens in plan mode (the Analyst, `app/tools/exit_plan_mode_tool/`) and develops the approved plan.

`print.ts` -> `runHeadless` is the model for the headless path (`develop_headless`): nobody is
asked anything, so the plan's approval is denied and a headless session plans without developing,
as upstream's `-p`. Interactively (`develop_interactive`) the conversation is the REPL
(`app/screens/repl.py`), where the user answers the Analyst's questions, approves the plan and
talks to the agents while they work. Before it opens, the project folder must be trusted
(`app/components/trust_dialog/trust_dialog.py`, upstream's `showSetupScreens`); then the plugin
startup checks run in the background (`performStartupChecks`, as `REPL.tsx` runs them on mount).

A session is resumed as upstream resumes one: its transcript, its TDD plan sidecar and its plan
file (`app/cli/print.py`), found again through the thread id. A session never restarts.
"""

from __future__ import annotations

import asyncio
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
    #: `--verbose`: the REPL expands collapsed searches and reads, call by call.
    verbose: bool = False
    #: `-p`'s output and input options (`app/cli/print.py`); `None` interactively.
    headless_options: Any = None
    #: `--system-prompt` / `--append-system-prompt` (their `-file` forms already read).
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    #: `--thinking` / `--max-thinking-tokens` (`app/utils/thinking.py`).
    thinking: str | None = None
    max_thinking_tokens: int | None = None
    #: `--agents <json>`: the session's `flagSettings` agents (`parseAgentsFromJson`).
    agents: Any = None


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
        "custom_system_prompt": options.system_prompt,
        "append_system_prompt": options.append_system_prompt,
        "thinking": options.thinking,
        "max_thinking_tokens": options.max_thinking_tokens,
        "agents": options.agents,
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
                                 options=options.headless_options,
                                 **{**development_run_options(options), **run_options}))


def rewind_files_headless(
    thread_id: str, user_message_id: str, workspace: Any, options: SessionOptions,
    write_out: Callable[[str], Any] | None = None,
) -> int:
    """`--rewind-files` (`print.ts`): the session's files put back at a user message; the exit code."""
    import sys

    from app.cli.print import run_rewind_files
    from app.utils.run_sync import run_sync

    return run_sync(run_rewind_files(thread_id, user_message_id, workspace=workspace,
                                     write_out=write_out or sys.stdout.write,
                                     write_err=sys.stderr.write, **development_run_options(options)))


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
    from app.cli.print import open_session_run, outcome_of, project_root_of, session_run_id
    from app.components.trust_dialog.trust_dialog import show_trust_dialog
    from app.hooks.use_can_use_tool import (
        PermissionAnswer,
        PermissionPrompt,
        build_interactive_can_use_tool,
    )
    from app.screens.repl import Repl
    from app.tdd.plan import TddPlan

    if io_factory is None:
        from app.bootstrap.state import get_original_cwd
        from app.history import PromptHistory
        from app.screens.terminal_io import TerminalIO

        # `history.ts`: ↑ shows this project's prompts, this session's first.
        prompt_history = PromptHistory(get_original_cwd(workspace), thread_id)

        def io_factory(on_interrupt: Callable[[], None]) -> Any:
            return TerminalIO(on_interrupt, prompt_history)
    holder: dict[str, Repl] = {}

    async def ask(permission: PermissionPrompt) -> PermissionAnswer:
        return await holder["repl"].ask_permission(permission)

    async def ask_questions(questions: tuple[dict[str, Any], ...]) -> dict[str, str] | None:
        return await holder["repl"].ask_questions(questions)

    opts = {**development_run_options(options), **run_options}
    escape = asyncio.Event()

    def on_interrupt() -> None:
        # Esc: the REPL's once it is open; before that, the trust dialog's.
        if "repl" in holder:
            holder["repl"].interrupt()
        else:
            escape.set()

    io = io_factory(on_interrupt)
    with io:
        # `showSetupScreens`: the trust dialog, before any of the project's settings, hooks or MCP servers run.
        project = project_root_of(workspace, opts.get("project_root"))
        await show_trust_dialog(io, project, escape)
        # Then the project's pending `.mcp.json` servers are approved or rejected, before any connects.
        from app.interactive_helpers import setup_settings_locations, show_mcpjson_server_approvals

        await show_mcpjson_server_approvals(io, escape, setup_settings_locations(
            project, home=opts.get("home"), managed_dir=opts.get("managed_dir"),
            settings_path=opts.get("settings_path")))
        async with open_session_run(run_id=session_run_id(thread_id), plan=TddPlan(), workspace=workspace,
                                    planning=True, can_use_tool=build_interactive_can_use_tool(ask, ask_questions),
                                    **opts) as run:
            repl = Repl(run, io, verbose=options.verbose)
            holder["repl"] = repl
            start_plugin_checks(run)
            terminal = await repl.run_session(request, prompt)
            return outcome_of(run, terminal)


def start_plugin_checks(run: Any) -> None:
    """
    `REPL.tsx`'s mount effect: `performStartupChecks` in the background, after the trust dialog (the
    marketplaces and plugins the settings declare are installed only with the folder trusted).
    """
    from app.utils.config import check_has_trust_dialog_accepted
    from app.utils.plugins.perform_startup_checks import perform_startup_checks

    # The run's own settings locations (its project, user home and policy directory).
    locations = run.mcp.settings_locations()
    project = locations.project_root
    task = asyncio.ensure_future(perform_startup_checks(
        run.store.update, locations, trust_accepted=check_has_trust_dialog_accepted(project)))
    _STARTUP_CHECKS.add(task)
    task.add_done_callback(_STARTUP_CHECKS.discard)


#: The background `performStartupChecks` (upstream's `void`), kept referenced.
_STARTUP_CHECKS: set[asyncio.Future[None]] = set()


__all__ = [
    "SessionOptions",
    "derive_thread_id",
    "develop_headless",
    "develop_interactive",
    "rewind_files_headless",
    "development_run_options",
    "fresh_thread_id",
    "session_exists",
    "start_plugin_checks",
]
