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
* **The model's questions** (`AskUserQuestion`, upstream's `AskUserQuestionPermissionRequest`):
  `ask_questions` shows one question at a time with its options and an "Other"; a number (or
  several, for a multiSelect question) picks, any other text is the user's own answer, and Esc
  declines.
* **Slash commands** (`app/commands.py`): never run mid-turn, as upstream's queue processor;
  the immediate ones (`/help`, `/plan`, `/pause`, `/exit`) do.

The session opens in plan mode (the Analyst): the model plans, asks with `AskUserQuestion`, and
asks for the plan's approval with `ExitPlanMode`, which `ask_permission` shows with upstream's
choices and `/edit`. The TDD plan is the session's (`app/tdd/plan.py`): the approved plan's
sub-requirements, which the loop moves through by the ledger; the user edits it with `/plan` and
the model with `UpdatePlan` (asked here). `/requirements <request>` is upstream's
`/plan <description>`: plan mode again, with the request; its approval replaces the pending items.
The REPL keeps running after the plan is done, so the user can add more.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from app.cli.print import (
    DevelopmentRun,
    project_root_of,
    start_session,
    submit_prompt,
)
from app.commands import (
    HELP_TEXT,
    Command,
    CommandError,
    SlashCommand,
    apply_plan_command,
    collect_context_data,
    find_command,
    format_context_as_markdown_table,
    get_commands,
    help_text,
    parse_slash_command,
)
from app.components.hooks.prompt_dialog import (
    PROMPT_CANCELLED,
    PromptCancelled,
    hook_prompts_enabled,
    parse_prompt_choice,
    render_prompt_dialog,
)
from app.components.messages.collapsed_read_search_content import (
    CollapsedReadSearchRenderer,
)
from app.components.permissions.permission_options import PermissionOption
from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt
from app.query.transitions import Terminal
from app.query_engine import ResultMessage
from app.session.prompts import development_continue_prompt
from app.tdd.plan import TddPlan, render_plan, update_tdd_plan
from app.tools.agent_tool.load_agents_dir import get_agent_definitions_with_overrides
from app.tools.agent_tool.ui import extract_last_tool_info
from app.types.hooks import PromptRequest, PromptResponse
from app.types.permissions import PermissionMode
from app.utils.collapse_read_search import summarize_recent_activities
from app.utils.message_queue_manager import QueuedCommand
from app.utils.model.model import parse_user_specified_model
from app.utils.permissions.permission_setup import set_permission_mode
from app.utils.process_user_input.process_slash_command import parse_slash_input

logger = logging.getLogger("TDDOrchestrator.REPL")

PROMPT = "❯ "
#: How much of a tool result the conversation view shows.
RESULT_PREVIEW_CHARS = 200

#: Upstream's plan-mode indicator (`⏸ plan mode on`).
PLAN_MODE_ON = "⏸ plan mode on"


class ReplIO(Protocol):
    """The terminal: one line in at a time, text out."""

    async def read_line(self, prompt: str) -> str | None:
        """The next line the user typed; `None` when they closed the input (Ctrl-D)."""

    def write(self, text: str) -> None:
        """Show `text` above the input line."""


def render_message(event: Any) -> str | None:
    """How a conversation event reads on the terminal; `None` for what the user need not see."""
    if isinstance(event, ResultMessage):
        # A turn that never queried (an unknown command) is answered by its result's text.
        return None if event.terminal_reason else (event.result or None)
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
    """
    The permission question, with its options numbered. A tool with a dialog shows it as
    upstream's `PermissionRequest` lays it out (the full call, then why it asks); the plan
    approval and a plan change show their own text.
    """
    asker = f" (the {prompt.agent} agent asks)" if prompt.agent else ""
    if prompt.dialog is not None:
        first, *rest = prompt.dialog.lines()
        lines = [f"⚠ {first}{asker}", *rest]
    else:
        lines = [f"⚠ Permission needed: {prompt.tool_name} — {prompt.description}{asker}"]
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


def _options(question: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(question.get("options") or [])


def render_question(question: Mapping[str, Any], number: int, total: int) -> str:
    """One of the model's questions, its options numbered and "Other" last."""
    options = _options(question)
    lines = [f"? [{question.get('header', '')}] {question.get('question', '')}"
             + (f" ({number}/{total})" if total > 1 else "")]
    lines += [f"  {i}. {o.get('label', '')} — {o.get('description', '')}" for i, o in enumerate(options, start=1)]
    lines.append(f"  {len(options) + 1}. Other — type your own answer")
    if question.get("multiSelect"):
        lines.append(f"Choose one or more of 1-{len(options)} (e.g. \"1,3\"), or type your own answer.")
    else:
        lines.append(f"Choose 1-{len(options)}, or type your own answer.")
    return "\n".join(lines)


def parse_question_answer(question: Mapping[str, Any], line: str) -> str | None:
    """
    The answer a line gives: an option's label, several labels joined by ", " for a
    multiSelect question, or the text itself ("Other"). `None` asks again (an empty line, a
    number out of range, or "Other" picked without its text).
    """
    text = line.strip()
    if not text:
        return None
    labels = [str(o.get("label", "")) for o in _options(question)]
    other = len(labels) + 1
    head, _, rest = text.partition(" ")
    if head.isdigit() and int(head) == other:
        return rest.strip() or None
    tokens = text.replace(",", " ").split() if question.get("multiSelect") else [text]
    if not all(token.isdigit() for token in tokens):
        return text
    picks = [int(token) for token in tokens]
    if not all(1 <= pick <= len(labels) for pick in picks):
        return None
    return ", ".join(labels[pick - 1] for pick in dict.fromkeys(picks))


@dataclass(frozen=True, slots=True)
class PendingAnswer:
    """A question waiting for the next line: how to read it, what to say when it reads nothing, and Esc's answer."""

    parse: Callable[[str], Any]
    future: asyncio.Future[Any]
    retry: str
    cancelled: Any


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

    def __init__(self, run: DevelopmentRun, io: ReplIO, *, verbose: bool = False) -> None:
        self.run = run
        self.io = io
        #: Consecutive searches and reads collapse into one summary; `--verbose` expands them.
        self.renderer = CollapsedReadSearchRenderer(
            lambda: getattr(self.run.ctx, "tools", ()) or (), lambda: self.run.ctx, render_message, verbose=verbose)
        self.turn: asyncio.Task[None] | None = None
        self.deferred: list[SlashCommand] = []
        self.pending: PendingAnswer | None = None
        #: One question on screen at a time: a background agent's prompt waits for the one shown
        #: (upstream's `toolUseConfirmQueue`).
        self._asking = asyncio.Lock()
        self.last_terminal: Terminal | None = None
        self.exiting = False
        #: `hasInterruptibleToolInProgressRef`: every executing tool would be cancelled by a new message.
        self.has_interruptible_tool_in_progress = False
        run.on_message = self._show
        run.ctx.set_has_interruptible_tool_in_progress = self._set_interruptible
        #: A foreground worker's messages so far, by its Agent call, and the line last shown for it.
        self._agent_progress: dict[str, list[Any]] = {}
        self._progress_shown: dict[str, str] = {}
        run.ctx.on_progress = self._on_progress
        if hook_prompts_enabled():
            run.ctx.request_prompt = self.request_prompt
        store = getattr(run, "store", None)
        if store is not None:
            store.subscribe(self._on_state_change)

    def _on_progress(self, tool_use_id: str, data: Mapping[str, Any]) -> None:
        """
        A foreground worker's progress (`AgentTool/UI.tsx` -> `extractLastToolInfo`): its last
        finished tool, or its rolled-up searches and reads, under the Agent call.
        """
        if data.get("type") != "agent_progress":
            return
        messages = self._agent_progress.setdefault(tool_use_id, [])
        messages.append(data.get("message"))
        line = extract_last_tool_info(messages, getattr(self.run.ctx, "tools", ()) or (), self.run.ctx)
        if line is not None and self._progress_shown.get(tool_use_id) != line:
            self._progress_shown[tool_use_id] = line
            self.io.write(f"  ⎿  {line}")

    def _on_state_change(self) -> None:
        """
        A background agent's status (`summarizeRecentActivities`, else its last activity), shown
        when it changes, as `<description>: <activity>`.
        """
        for task in self.run.store.get().tasks.values():
            progress = getattr(task, "progress", None)
            if task.type != "local_agent" or task.status != "running" or progress is None:
                continue
            activities = [a.as_dict() for a in progress.recent_activities]
            last = progress.last_activity.activity_description if progress.last_activity is not None else None
            activity = summarize_recent_activities(activities) or last
            key = f"task:{task.id}"
            if activity and self._progress_shown.get(key) != activity:
                self._progress_shown[key] = activity
                self.io.write(f"  ⎿  {task.description}: {activity}")

    def _set_interruptible(self, value: bool) -> None:
        self.has_interruptible_tool_in_progress = value

    @property
    def busy(self) -> bool:
        return self.turn is not None and not self.turn.done()

    @property
    def plan(self) -> TddPlan:
        plan: TddPlan = self.run.store.get().tdd_plan
        return plan

    def _show(self, event: Any) -> None:
        for line in self.renderer.feed(event):
            self.io.write(line)

    # ── permissions ────────────────────────────────────────────────────────

    async def _wait_for_answer(self, text: str, parse: Callable[[str], Any], retry: str, cancelled: Any) -> Any:
        async with self._asking:
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self.pending = PendingAnswer(parse=parse, future=future, retry=retry, cancelled=cancelled)
            self.io.write(text)
            try:
                return await future
            finally:
                self.pending = None

    async def ask_permission(self, prompt: PermissionPrompt) -> PermissionAnswer:
        """The run's `canUseTool` prompt: shown now, answered by the next line typed."""
        answer: PermissionAnswer = await self._wait_for_answer(
            render_permission_prompt(prompt), lambda line: parse_permission_answer(prompt.options, line),
            f"Choose 1-{len(prompt.options)}.", PermissionAnswer("no"),
        )
        return answer

    def request_prompt(
        self, title: str, tool_input_summary: str | None
    ) -> Callable[[PromptRequest], Awaitable[PromptResponse]]:
        """
        `REPL.tsx`'s `requestPrompt(title, toolInputSummary)`: a hook's question, queued behind any
        other question on screen and answered by number; Esc rejects it ("Prompt cancelled by
        user") and aborts the turn.
        """

        async def ask(request: PromptRequest) -> PromptResponse:
            cancelled = object()
            answer = await self._wait_for_answer(
                render_prompt_dialog(title, tool_input_summary, request),
                partial(parse_prompt_choice, request.options), f"Choose 1-{len(request.options)}.", cancelled,
            )
            if answer is cancelled:
                raise PromptCancelled(PROMPT_CANCELLED)
            return PromptResponse(prompt_response=request.prompt, selected=str(answer))

        return ask

    async def ask_questions(self, questions: Sequence[Mapping[str, Any]]) -> dict[str, str] | None:
        """`AskUserQuestion`'s questions, one at a time: question text -> answer, or `None` on Esc."""
        answers: dict[str, str] = {}
        for number, question in enumerate(questions, start=1):
            parse: Callable[[str], str | None] = partial(parse_question_answer, question)
            answer = await self._wait_for_answer(
                render_question(question, number, len(questions)), parse,
                f"Choose 1-{len(_options(question)) + 1}, or type your own answer.", None,
            )
            if answer is None:
                return None
            answers[str(question.get("question", ""))] = answer
        return answers

    # ── turns ──────────────────────────────────────────────────────────────

    def _start(self, turn: Coroutine[Any, Any, Terminal | None]) -> None:
        self.turn = asyncio.create_task(self._run_turn(turn))

    async def _run_turn(self, turn: Coroutine[Any, Any, Terminal | None]) -> None:
        """Run one turn; `None` is a resumed session that was only loaded, and reports nothing."""
        try:
            terminal = await turn
        except Exception:  # a crashed turn is reported, and the session goes on
            logger.exception("Turn crashed")
            terminal = Terminal.MODEL_ERROR
        for line in self.renderer.flush():
            self.io.write(line)
        if terminal is not None:
            self.last_terminal = terminal
            self.io.write(turn_end_text(terminal, self.plan, self.run.store.get().tdd_paused))
        deferred, self.deferred = self.deferred, []
        self.turn = None
        for command in deferred:
            await self.run_command(command)

    def interrupt(self) -> None:
        """Esc: a pending question is answered No, and the turn is aborted (upstream `onReject` + abort)."""
        if self.pending is not None and not self.pending.future.done():
            self.pending.future.set_result(self.pending.cancelled)
        if self.busy:
            self.run.engine.interrupt()

    async def _stop_turn(self) -> None:
        if self.busy:
            self.interrupt()
            assert self.turn is not None
            await self.turn

    # ── input ──────────────────────────────────────────────────────────────

    async def handle_line(self, line: str) -> None:
        """One line from the user: an answer, a command, or a message to the agents."""
        if self.pending is not None:
            pending = self.pending
            answer = pending.parse(line)
            if answer is None:
                self.io.write(pending.retry)
            elif not pending.future.done():
                pending.future.set_result(answer)
            return
        command = parse_slash_command(line) or self._typed_skill(line)
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
            # `handlePromptSubmit` while busy: when every executing tool is a 'cancel' one, the
            # turn is aborted ('interrupt') first; then the message is queued for the loop to
            # hand over between tool rounds.
            if self.has_interruptible_tool_in_progress:
                self.run.engine.interrupt("interrupt")
            self.run.ctx.command_queue.enqueue(QueuedCommand(value=text, mode="prompt", priority="next"))
            self.io.write("(queued for the agents)")
            return
        self._start(submit_prompt(self.run, text))

    def _commands(self) -> list[Command]:
        return get_commands(getattr(self.run.ctx, "skill_registry", None))

    def _typed_skill(self, line: str) -> SlashCommand | None:
        """`/<skill> args`: a prompt command when a user-invocable skill has that name."""
        parsed = parse_slash_input(line)
        if parsed is None:
            return None
        command = find_command(parsed[0], self._commands())
        if command is None or command.type != "prompt" or command.is_hidden:
            return None
        return SlashCommand(name=command.name, args=parsed[1], kind="prompt")

    async def run_command(self, command: SlashCommand) -> None:
        name = command.name
        if command.kind == "prompt":
            # `processUserInput` expands the skill (`processSlashCommand`).
            self._start(submit_prompt(self.run, f"/{command.name} {command.args}".rstrip()))
        elif name == "help":
            self.io.write(help_text(self._commands()))
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
        elif name == "context":
            await self._context()
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

    async def _context(self) -> None:
        """
        `/context` (`context-noninteractive.ts` -> `call`): the session's context usage, as Markdown.
        The model is the one the next turn calls (`getMainLoopModel`), the tools the pool it is
        offered (`refreshTools`), the agents those the Agent tool lists (`appState.agentDefinitions`).
        """
        run = self.run
        ctx = run.ctx
        model = parse_user_specified_model(run.user_specified_model) if run.user_specified_model else run.config.model
        tools = ctx.refresh_tools() if ctx.refresh_tools is not None else ctx.tools
        definitions = get_agent_definitions_with_overrides(project_dir=project_root_of(ctx.workspace))
        data = await collect_context_data(
            run.messages, main_loop_model=model, tools=tools, agent_definitions=definitions, tool_context=ctx)
        self.io.write(format_context_as_markdown_table(data))

    async def _requirements(self, request: str) -> None:
        """Upstream's `/plan <description>`: plan mode again (its approval re-plans), then the request."""
        if not request:
            self.io.write("Usage: /requirements <what to change>")
            return
        self.run.store.update(lambda state: set_permission_mode(state, PermissionMode.PLAN))
        self.io.write(PLAN_MODE_ON)
        self._start(submit_prompt(self.run, request))

    # ── the session ────────────────────────────────────────────────────────

    async def run_session(self, request: str | None, prompt: str | None = None) -> Terminal | None:
        """
        Start the conversation with `request`, or resume it (with `prompt`, the user's next message),
        and read the user's lines until they leave. A resumed session with nothing to say waits.
        """
        in_plan = self.run.store.get().tool_permission_context.mode == PermissionMode.PLAN
        header = PLAN_MODE_ON if in_plan else f"TDD plan:\n{render_plan(self.plan)}"
        self.io.write(f"{header}\n\n{HELP_TEXT}")
        self._start(start_session(self.run, request, prompt))
        while not self.exiting:
            line = await self.io.read_line(PROMPT)
            if line is None:
                break
            await self.handle_line(line)
        await self._stop_turn()
        return self.last_terminal
