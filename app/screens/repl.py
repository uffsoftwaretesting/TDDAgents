"""
The interactive development session: one conversation the user can talk to at any time
(Phase 11).

Ported in shape from `reference/claude-code/src/screens/REPL.tsx` and the modules it drives:

* **One conversation.** The REPL holds one `DevelopmentRun` (`app/cli/print.py`) for the
  session and submits each user turn over its history (`onQuery` → `query()`).
* **Typing while the agents work** (`utils/handlePromptSubmit.ts`): the line is queued at
  `next` on the run's command queue, so the loop hands it to the model between tool rounds
  (the `queued_command` attachment); a turn that ends first picks it up as the next turn.
* **Esc interrupts** (`hooks/useCancelRequest.ts`): the turn's abort signal fires and control
  returns to the prompt; a pending permission question is answered "No".
* **Permission questions** (`components/permissions/`): `ask_permission` is the run's
  `canUseTool` prompt — the options are numbered and the next line typed answers them.
* **Slash commands** (`app/commands.py`): never run mid-turn, as upstream's queue processor;
  the immediate ones (`/help`, `/plan`, `/pause`, `/exit`) do.

The TDD plan is the session's (`app/tdd/plan.py`): the loop moves through it by the ledger,
the user edits it with `/plan` and the model with `UpdatePlan` (asked here). `/requirements`
re-runs the analyst, engineer and planner (`rescope`) and the new plan replaces the pending
items. The REPL keeps running after the plan is done, so the user can add more.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import replace
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from app.cli.print import DevelopmentRun, begin_session, submit_prompt
from app.commands import (
    HELP_TEXT,
    CommandError,
    SlashCommand,
    apply_plan_command,
    parse_slash_command,
)
from app.components.permissions.permission_options import PermissionOption
from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt
from app.query.transitions import Terminal
from app.session.prompts import (
    development_continue_prompt,
    development_replanned_prompt,
)
from app.tdd.plan import TddPlan, render_plan, update_tdd_plan
from app.utils.message_queue_manager import QueuedCommand

logger = logging.getLogger("TDDOrchestrator.REPL")

PROMPT = "❯ "
#: How much of a tool result the conversation view shows.
RESULT_PREVIEW_CHARS = 200

#: `/requirements`: the request in, the new (specification, plan) out — `None` when the analysis
#: did not reach a plan (the user left the analyst's question, or it failed).
Rescope = Callable[[str], Awaitable[tuple[str, list[str]] | None]]


class ReplIO(Protocol):
    """The terminal: one line in at a time, text out."""

    async def read_line(self, prompt: str) -> str | None:
        """The next line the user typed; `None` when they closed the input (Ctrl-D)."""

    def write(self, text: str) -> None:
        """Show `text` above the input line."""


def render_message(event: Any) -> str | None:
    """How a conversation event reads on the terminal; `None` for what the user need not see."""
    if not isinstance(event, BaseMessage) or event.additional_kwargs.get("is_meta"):
        return None
    if isinstance(event, AIMessage):
        lines = []
        text = event.content if isinstance(event.content, str) else "".join(
            str(b.get("text", "")) for b in event.content if isinstance(b, dict) and b.get("type") == "text")
        if text.strip():
            lines.append(f"● {text.strip()}")
        for call in event.tool_calls:
            args = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in call.get("args", {}).items())
            lines.append(f"⏺ {call.get('name')}({args})")
        return "\n".join(lines) or None
    if isinstance(event, ToolMessage):
        content = str(event.content).strip()
        preview = content.splitlines()[0][:RESULT_PREVIEW_CHARS] if content else "(no output)"
        marker = "✗" if event.status == "error" else "⎿"
        return f"  {marker} {preview}"
    return None


def render_permission_prompt(prompt: PermissionPrompt) -> str:
    """The permission question, with its options numbered."""
    lines = [f"⚠ Permission needed: {prompt.tool_name} — {prompt.description}"]
    if prompt.message:
        lines.append(prompt.message)
    lines += [f"  {i}. {option.label}" for i, option in enumerate(prompt.options, start=1)]
    lines.append(f"Choose 1-{len(prompt.options)}; after a No you may add what to do instead (e.g. "
                 f"\"{len(prompt.options)} use the existing helper\").")
    return "\n".join(lines)


def parse_permission_answer(options: Sequence[PermissionOption], line: str) -> PermissionAnswer | None:
    """
    `2` picks option 2; `3 do X instead` picks it with feedback. Text that is not a number is
    the user saying no, and what to do instead. `None` asks again (an empty line, an unknown
    number).
    """
    text = line.strip()
    if not text:
        return None
    head, _, rest = text.partition(" ")
    if head.isdigit():
        index = int(head) - 1
        if not 0 <= index < len(options):
            return None
        return PermissionAnswer(options[index].value, rest.strip() or None)
    no = next((o for o in options if o.reject), None)
    return PermissionAnswer(no.value if no is not None else "no", text)


def turn_end_text(terminal: Terminal, plan: TddPlan, paused: bool) -> str:
    """What the user is told when a turn ends."""
    if terminal in (Terminal.ABORTED_STREAMING, Terminal.ABORTED_TOOLS):
        return "Interrupted. Tell the agents what to do next."
    if plan.items and plan.is_complete:
        return "✔ Every sub-requirement is done. Add more with /plan add, or /exit."
    current = plan.current
    position = f"{plan.done_count}/{len(plan.items)} done" if plan.items else "no plan"
    where = f" — current: {current.id}. {current.text}" if current is not None else ""
    status = "Paused" if paused else f"Stopped ({terminal})"
    return f"{status}: {position}{where}. Type to continue, /resume, or /help."


class Repl:
    """One development session at the terminal."""

    def __init__(self, run: DevelopmentRun, io: ReplIO, *, specification: str = "",
                 rescope: Rescope | None = None) -> None:
        self.run = run
        self.io = io
        self.specification = specification
        self.rescope = rescope
        self.turn: asyncio.Task[None] | None = None
        self.deferred: list[SlashCommand] = []
        self.pending: tuple[PermissionPrompt, asyncio.Future[PermissionAnswer]] | None = None
        self.last_terminal: Terminal | None = None
        self.exiting = False
        run.on_message = self._show

    @property
    def busy(self) -> bool:
        return self.turn is not None and not self.turn.done()

    @property
    def plan(self) -> TddPlan:
        plan: TddPlan = self.run.store.get().tdd_plan
        return plan

    def _show(self, event: Any) -> None:
        text = render_message(event)
        if text is not None:
            self.io.write(text)

    # ── permissions ────────────────────────────────────────────────────────

    async def ask_permission(self, prompt: PermissionPrompt) -> PermissionAnswer:
        """The run's `canUseTool` prompt: shown now, answered by the next line typed."""
        future: asyncio.Future[PermissionAnswer] = asyncio.get_running_loop().create_future()
        self.pending = (prompt, future)
        self.io.write(render_permission_prompt(prompt))
        try:
            return await future
        finally:
            self.pending = None

    # ── turns ──────────────────────────────────────────────────────────────

    def _start(self, turn: Coroutine[Any, Any, Terminal]) -> None:
        self.turn = asyncio.create_task(self._run_turn(turn))

    async def _run_turn(self, turn: Coroutine[Any, Any, Terminal]) -> None:
        try:
            terminal = await turn
        except Exception:  # a crashed turn is reported, and the session goes on
            logger.exception("Turn crashed")
            terminal = Terminal.MODEL_ERROR
        self.last_terminal = terminal
        self.io.write(turn_end_text(terminal, self.plan, self.run.store.get().tdd_paused))
        deferred, self.deferred = self.deferred, []
        self.turn = None
        for command in deferred:
            await self.run_command(command)

    def interrupt(self) -> None:
        """Esc: a pending question is answered No, and the turn is aborted (upstream `onReject` + abort)."""
        if self.pending is not None and not self.pending[1].done():
            self.pending[1].set_result(PermissionAnswer("no"))
        if self.busy:
            self.run.ctx.cancel.cancel()

    async def _stop_turn(self) -> None:
        if self.busy:
            self.interrupt()
            assert self.turn is not None
            await self.turn

    # ── input ──────────────────────────────────────────────────────────────

    async def handle_line(self, line: str) -> None:
        """One line from the user: an answer, a command, or a message to the agents."""
        if self.pending is not None:
            prompt, future = self.pending
            answer = parse_permission_answer(prompt.options, line)
            if answer is None:
                self.io.write(f"Choose 1-{len(prompt.options)}.")
            elif not future.done():
                future.set_result(answer)
            return
        command = parse_slash_command(line)
        if command is not None:
            if self.busy and not command.immediate:
                self.deferred.append(command)
                self.io.write(f"(/{command.name} runs when the current turn ends)")
                return
            await self.run_command(command)
            return
        text = line.strip()
        if not text:
            return
        if self.busy:
            # `handlePromptSubmit` while busy: queued for the loop to hand over between tool rounds.
            self.run.ctx.command_queue.enqueue(QueuedCommand(value=text, mode="prompt", priority="next"))
            self.io.write("(queued for the agents)")
            return
        self._start(submit_prompt(self.run, text))

    async def run_command(self, command: SlashCommand) -> None:
        name = command.name
        if name == "help":
            self.io.write(HELP_TEXT)
        elif name == "plan":
            self._plan_command(command.args)
        elif name == "pause":
            self.run.ctx.set_app_state(lambda s: replace(s, tdd_paused=True))
            self.io.write("Paused: the agents will stop at the end of this turn. /resume to continue.")
        elif name == "resume":
            self.run.ctx.set_app_state(lambda s: replace(s, tdd_paused=False))
            if self.busy:
                self.io.write("Resumed.")
            else:
                self._start(submit_prompt(self.run, development_continue_prompt(self.plan)))
        elif name == "requirements":
            await self._requirements(command.args)
        else:  # exit, quit
            self.exiting = True
            await self._stop_turn()

    def _plan_command(self, args: str) -> None:
        if not args:
            self.io.write(render_plan(self.plan))
            return
        try:
            updated = update_tdd_plan(self.run.ctx, lambda plan: apply_plan_command(plan, args))
        except CommandError as exc:
            self.io.write(str(exc))
            return
        self.io.write(render_plan(updated))

    async def _requirements(self, request: str) -> None:
        if self.rescope is None:
            self.io.write("/requirements is not available in this session.")
            return
        if not request:
            self.io.write("Usage: /requirements <what to change>")
            return
        outcome = await self.rescope(request)
        if outcome is None:
            self.io.write("The requirements were not changed.")
            return
        self.specification, texts = outcome
        updated = update_tdd_plan(self.run.ctx, lambda plan: plan.replace_pending(texts))
        self.io.write(f"New plan:\n{render_plan(updated)}")
        self._start(submit_prompt(self.run, development_replanned_prompt(self.specification, updated)))

    # ── the session ────────────────────────────────────────────────────────

    async def run_session(self, fresh_prompt: str, resume_prompt: str | None = None) -> Terminal | None:
        """Start (or resume) the conversation and read the user's lines until they leave."""
        self.io.write(f"TDD plan:\n{render_plan(self.plan)}\n\n{HELP_TEXT}")
        self._start(begin_session(self.run, fresh_prompt, resume_prompt))
        while not self.exiting:
            line = await self.io.read_line(PROMPT)
            if line is None:
                break
            await self.handle_line(line)
        await self._stop_turn()
        return self.last_terminal
