"""
Interrupting and steering the agents, and input that never breaks the REPL (2026-11-17).

Upstream's ground truth, `reference/claude-code/src/`:
* Esc → `REPL.tsx`'s `onCancel` → `abort('user-cancel')`; the request (`claude.ts`) and the commands
  (`ShellCommand.ts`'s `treeKill`) stop at once; the next prompt is an ordinary turn. `/interrupt` is the
  same as a typed command (user decision).
* `'now'` priority (`types/textInputTypes.ts`): "interrupt and send immediately"; `abort('interrupt')`
  (`REPL.tsx`, `print.ts`), no interruption message, the message first as the next turn. `/send-anyways`
  sends it (user decision).
* At a pending question a command is never an answer, nothing typed crashes the session, and an invalid
  line repeats the question's options and says Esc cancels (user decision).
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.query.transitions import Terminal
from app.screens.repl import ESC_CANCELS, Repl, question_reminder
from app.services.api.claude import stop_on_cancel
from app.tdd.plan import TddPlan
from app.tool import CancelToken
from app.utils.messages import INTERRUPT_MESSAGE, REJECT_MESSAGE_WITH_REASON_PREFIX
from app.workspace.base import WorkspaceCancelled
from app.workspace.local import LocalWorkspace
from tests.test_repl import CYCLE, FakeIO, Model, call, planning_turns, session, until

DONE = TddPlan.from_texts(["A"]).complete_current(CYCLE)
SLOW_TEST = "import time\n\ndef test_slow():\n    time.sleep({seconds})\n"


def _texts(state: Any) -> list[str]:
    return [str(m.content) for m in state.messages if isinstance(m, HumanMessage)]


# ── the request stops on cancel (claude.ts's abort signal) ──────────────────

def test_the_model_stream_ends_as_soon_as_the_cancel_fires():
    async def slow() -> Any:
        yield "first"
        await asyncio.sleep(30)
        yield "never"

    async def go() -> tuple[list[str], float]:
        cancel = CancelToken()
        seen: list[str] = []
        started = time.monotonic()
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, cancel.cancel, "user-cancel")
        async for item in stop_on_cancel(slow(), cancel):
            seen.append(item)
        return seen, time.monotonic() - started

    seen, took = asyncio.run(go())
    assert seen == ["first"] and took < 2


def test_the_stream_is_read_whole_without_a_cancel_and_not_at_all_after_one():
    async def three() -> Any:
        for item in ("a", "b", "c"):
            yield item

    async def collect(cancel: Any) -> list[str]:
        return [item async for item in stop_on_cancel(three(), cancel)]

    assert asyncio.run(collect(None)) == ["a", "b", "c"]
    assert asyncio.run(collect(CancelToken())) == ["a", "b", "c"]
    fired = CancelToken()
    fired.cancel("user-cancel")
    assert asyncio.run(collect(fired)) == []


def test_the_aborted_stream_is_closed():
    closed: list[bool] = []

    async def provider() -> Any:
        try:
            yield "x"
            await asyncio.sleep(30)
        finally:
            closed.append(True)

    async def go() -> None:
        cancel = CancelToken()
        async for _ in stop_on_cancel(provider(), cancel):
            cancel.cancel("user-cancel")

    asyncio.run(go())
    assert closed == [True]


# ── commands are killed on cancel (ShellCommand.ts's treeKill) ──────────────

def _cancel_after(token: CancelToken, seconds: float, reason: str) -> None:
    threading.Timer(seconds, token.cancel, args=(reason,)).start()


def test_a_running_command_is_killed_when_the_user_cancels(tmp_path: Path):
    ws = LocalWorkspace(str(tmp_path))
    token = CancelToken()
    _cancel_after(token, 0.3, "user-cancel")
    started = time.monotonic()
    with pytest.raises(WorkspaceCancelled):
        ws.execute("sleep 5; echo finished > done.txt", timeout=30, cancel=token)
    assert time.monotonic() - started < 3
    time.sleep(0.2)
    assert not (tmp_path / "done.txt").exists()  # the whole tree died with it


def test_a_message_sent_now_lets_a_running_command_finish(tmp_path: Path):
    ws = LocalWorkspace(str(tmp_path))
    token = CancelToken()
    _cancel_after(token, 0.1, "interrupt")  # upstream: 'block' tools finish on 'interrupt'
    result = ws.execute("sleep 0.4; echo finished", timeout=30, cancel=token)
    assert result.exit_code == 0 and result.stdout.strip() == "finished"
    assert ws.execute("echo plain", timeout=30).stdout.strip() == "plain"


def test_run_tests_is_interrupted_and_creates_no_evidence(python_workspace):
    from app.tdd.run_tests_tool.run_tests_tool import INTERRUPTED_RUN, RunTestsUnavailable, run_suite
    from tests.plan_helpers import TEST_PROJECT_CONFIG

    root = Path(str(python_workspace.root))
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_slow.py").write_text(SLOW_TEST.format(seconds=20))
    token = CancelToken()
    _cancel_after(token, 0.5, "user-cancel")
    started = time.monotonic()
    with pytest.raises(RunTestsUnavailable, match=INTERRUPTED_RUN):
        run_suite(python_workspace, TEST_PROJECT_CONFIG, cancel=token)
    assert time.monotonic() - started < 10


# ── /interrupt: stop, stay open, a message continues ─────────────────────────

def test_interrupt_stops_a_running_test_suite_and_a_message_continues(python_workspace, tmp_path):
    root = Path(str(python_workspace.root))
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_slow.py").write_text(SLOW_TEST.format(seconds=20))
    model = Model([[call("RunTests", "r1")], [AIMessage(content="back at it")]])
    timings: dict[str, float] = {}

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: (repl.status_line() or "").startswith("⛨ RunTests…"), timeout=30)
        timings["sent"] = time.monotonic()
        io.lines.put_nowait("/interrupt")
        await until(lambda: not repl.busy, timeout=15)
        timings["stopped"] = time.monotonic()
        assert repl.last_terminal in (Terminal.ABORTED_TOOLS, Terminal.ABORTED_STREAMING)
        io.lines.put_nowait("carry on")
        await until(lambda: len(model.seen) == 2 and not repl.busy)
        io.lines.put_nowait("/interrupt")  # nothing runs now
        await until(lambda: "Nothing is running." in io.out)
        io.lines.put_nowait("/exit")

    terminal, repl, io = asyncio.run(session(python_workspace, tmp_path, model, DONE, script))
    assert timings["stopped"] - timings["sent"] < 8  # the 20 s suite was killed, not awaited
    assert "Interrupted. Tell the agents what to do next." in io.text()
    assert "carry on" in _texts(model.seen[1])
    assert terminal == Terminal.COMPLETED  # TDDAgents stayed open and the next turn ran normally


# ── /send-anyways: the 'now' priority ────────────────────────────────────────

def test_send_anyways_sends_a_message_now_without_an_interruption_message(python_workspace, tmp_path):
    started = asyncio.Event()

    async def runs_until_cancelled(state: Any) -> list[Any]:
        started.set()
        while not state.tool_context.cancel.cancelled:
            await asyncio.sleep(0.01)
        return [AIMessage(content="cut off")]

    model = Model([runs_until_cancelled, [AIMessage(content="new course")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await started.wait()
        io.lines.put_nowait("/send-anyways use SQLite instead")
        await until(lambda: "(sent to the agents now)" in io.out)
        await until(lambda: len(model.seen) == 2 and not repl.busy)
        io.lines.put_nowait("/exit")

    asyncio.run(session(python_workspace, tmp_path, model, DONE, script))
    assert model.seen[0].tool_context.cancel.reason == "interrupt"
    second = _texts(model.seen[1])
    assert second[-1] == "use SQLite instead"
    assert INTERRUPT_MESSAGE not in second  # 'interrupt' adds no interruption message


def test_send_anyways_without_text_sends_the_queued_messages_now(python_workspace, tmp_path):
    started = asyncio.Event()

    async def runs_until_cancelled(state: Any) -> list[Any]:
        started.set()
        while not state.tool_context.cancel.cancelled:
            await asyncio.sleep(0.01)
        return [AIMessage(content="cut off")]

    model = Model([runs_until_cancelled, [AIMessage(content="ok")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await started.wait()
        io.lines.put_nowait("first idea")
        await until(lambda: "(queued for the agents)" in io.out)
        io.lines.put_nowait("/send-anyways")
        await until(lambda: "(sent 1 queued message to the agents now)" in io.out)
        await until(lambda: len(model.seen) == 2 and not repl.busy)
        io.lines.put_nowait("/send-anyways")  # idle: nothing to send
        await until(lambda: io.out.count("Nothing is running.") == 1)
        io.lines.put_nowait("/exit")

    asyncio.run(session(python_workspace, tmp_path, model, DONE, script))
    assert "first idea" in _texts(model.seen[1])


def test_send_anyways_while_idle_is_an_ordinary_message(python_workspace, tmp_path):
    model = Model([[AIMessage(content="a")], [AIMessage(content="b")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: not repl.busy and repl.last_terminal is not None)
        io.lines.put_nowait("/send-anyways hello")
        await until(lambda: len(model.seen) == 2 and not repl.busy)
        io.lines.put_nowait("/exit")

    asyncio.run(session(python_workspace, tmp_path, model, DONE, script))
    assert _texts(model.seen[1])[-1] == "hello"


def test_a_now_command_is_never_taken_as_an_attachment():
    from types import SimpleNamespace

    from app.utils.attachments import get_queued_command_attachments
    from app.utils.message_queue_manager import CommandQueue, QueuedCommand

    queue = CommandQueue()
    queue.enqueue(QueuedCommand("steer", "prompt", priority="now"))
    queue.enqueue(QueuedCommand("later idea", "prompt", priority="next"))
    context = SimpleNamespace(command_queue=queue, agent_id=None, cancel=CancelToken())
    attachments = get_queued_command_attachments([], context)
    assert [a.data["prompt"] for a in attachments] == ["later idea"]
    assert [c.value for c in queue.snapshot()] == ["steer"]
    context.cancel.cancel("interrupt")
    queue.enqueue(QueuedCommand("after abort", "prompt", priority="next"))
    assert get_queued_command_attachments([], context) == []  # the aborted turn takes nothing


# ── a pending question: commands, odd input, and Esc ────────────────────────

def test_commands_at_a_pending_question_are_never_answers(python_workspace, tmp_path):
    model = Model([[call("Bash", "b1", command="make build")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: repl.pending is not None)
        io.lines.put_nowait("/edit")
        await until(lambda: any(o.startswith("Unknown command /edit") for o in io.out))
        assert repl.pending is not None  # still asked
        io.lines.put_nowait("0")  # out of range
        io.lines.put_nowait("9")
        await until(lambda: sum(o.endswith(f"Choose 1-3. {ESC_CANCELS}") for o in io.out) >= 3)
        io.lines.put_nowait("/help")
        await until(lambda: any("/send-anyways [text]" in o for o in io.out))
        assert repl.pending is not None
        io.lines.put_nowait("/interrupt")  # cancels the question, as Esc
        await until(lambda: not repl.busy)
        io.lines.put_nowait("/exit")

    terminal, _, io = asyncio.run(session(python_workspace, tmp_path, model, DONE, script))
    assert terminal == Terminal.ABORTED_TOOLS
    results = [m for m in model.seen[0].messages if isinstance(m, ToolMessage)]
    assert not any("/edit" in str(m.content) or "/help" in str(m.content) for m in results)


def test_a_superscript_digit_never_crashes_a_parser():
    """`"²".isdigit()` is True and `int("²")` raises: every parser now reads it as no number."""
    from app.components.design_system.dialog import is_option_number, parse_choice
    from app.components.hooks.prompt_dialog import parse_prompt_choice
    from app.components.mcp_server_multiselect_dialog import parse_selection
    from app.components.permissions.permission_options import NO, YES
    from app.components.permissions.sandbox_permission_request import (
        parse_sandbox_permission_answer,
        sandbox_permission_options,
    )
    from app.screens.repl import parse_permission_answer, parse_question_answer
    from app.types.hooks import PromptOption

    assert not is_option_number("²") and not is_option_number("①") and is_option_number("12")
    assert parse_choice(3, "²") is None and parse_choice(3, "2") == 1
    assert parse_prompt_choice((PromptOption("a", "A"),), "²") is None
    assert parse_selection(["s"], "²") is None
    assert parse_sandbox_permission_answer(sandbox_permission_options("h", False), "²") is None
    answer = parse_permission_answer((YES, NO), "²")  # text is "No, do this instead", never a crash
    assert answer is not None and answer.value == NO.value and answer.feedback == "²"
    question = {"question": "Q?", "header": "H", "options": [{"label": "A", "description": ""}]}
    assert parse_question_answer(question, "²") == "²"  # "Other": the text itself


def test_exit_at_a_pending_question_leaves(python_workspace, tmp_path):
    model = Model([[call("Bash", "b1", command="make build")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: repl.pending is not None)
        io.lines.put_nowait("/exit")

    terminal, repl, _ = asyncio.run(session(python_workspace, tmp_path, model, DONE, script))
    assert repl.exiting and terminal == Terminal.ABORTED_TOOLS


def test_the_question_reminder_keeps_its_question_and_options():
    text = "⚠ Bash command\nmake build\nDo you want to proceed?\n  1. Yes\n  2. No\nChoose 1-2; after a No …"
    assert question_reminder(text, "Choose 1-2.") == (
        "⚠ Bash command\nDo you want to proceed?\n  1. Yes\n  2. No\nChoose 1-2. Esc to cancel.")
    assert question_reminder("Plain", "") == "Esc to cancel."
    assert question_reminder("? [H] Which?\n  1) A", "Pick.") == "? [H] Which?\n  1) A\nPick. Esc to cancel."
    plan = ("⚠ Permission needed: ExitPlanMode — Exit\nHere is the plan:\n1. Parse\n2. Validate?\n3. Report\n"
            "Would you like to proceed?\n  1. Yes\n  3. No, keep planning\nChoose 1-3.")
    # The plan's own numbered items (even one ending in "?") are not the question's options.
    assert question_reminder(plan, "Choose 1-3.") == (
        "⚠ Permission needed: ExitPlanMode — Exit\nWould you like to proceed?\n  1. Yes\n  3. No, keep planning\n"
        "Choose 1-3. Esc to cancel.")


# ── the plan approval: typed requirements revise the plan ───────────────────

def test_typed_requirements_at_the_plan_approval_reach_the_analyst(python_workspace, tmp_path):
    model = Model([[AIMessage(content="a")], *planning_turns("X"), [AIMessage(content="revising")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: not repl.busy and repl.last_terminal is not None)
        io.lines.put_nowait("/requirements add X")
        await until(lambda: repl.pending is not None)
        io.lines.put_nowait("/edit")  # no plan editor: unknown, asked again
        await until(lambda: any(o.startswith("Unknown command /edit") for o in io.out))
        io.lines.put_nowait("3")  # a No without what to change: asked again (upstream's no-op)
        await until(lambda: any("Tell the Analyst what to change" in o for o in io.out))
        io.lines.put_nowait("also support division by zero errors")
        await until(lambda: len(model.seen) >= 4 and not repl.busy)
        io.lines.put_nowait("/exit")

    _, repl, io = asyncio.run(session(python_workspace, tmp_path, model, DONE, script))
    feedback = [m for m in model.seen[3].messages if isinstance(m, ToolMessage)][-1]
    assert str(feedback.content) == f"{REJECT_MESSAGE_WITH_REASON_PREFIX}also support division by zero errors"
    assert "(Type /edit" not in io.text()
