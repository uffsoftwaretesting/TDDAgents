"""The 'interrupt' abort reason (Part 8c): a message submitted while a turn runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.cli.print import interrupt_on_now_priority
from app.query.config import RunConfig
from app.query.query import abort_reason
from app.query.state import LoopState
from app.query.transitions import Terminal, Terminated
from app.services.tools.streaming_tool_executor import (
    StreamingToolExecutor,
    TrackedTool,
)
from app.state.app_state_store import AppStateStore
from app.tool import CancelToken, ToolContext, ToolResult, build_tool, tool_context_for
from app.utils.abort_controller import AbortController, bridge_cancel_token
from app.utils.message_queue_manager import CommandQueue, QueuedCommand
from app.utils.messages import (
    INTERRUPT_MESSAGE,
    INTERRUPT_MESSAGE_FOR_TOOL_USE,
    Message,
    ToolCall,
    create_user_interruption_message,
)
from tests.test_loop_engine import FakeModel, asks_for, collect, start


def _interruption(tool_use: bool) -> HumanMessage:
    text = INTERRUPT_MESSAGE_FOR_TOOL_USE if tool_use else INTERRUPT_MESSAGE
    return HumanMessage(content=[{"type": "text", "text": text}])


# ── the reason itself ────────────────────────────────────────────────────────

def test_a_cancel_token_keeps_its_first_reason():
    token = CancelToken()
    assert (token.cancelled, token.reason) == (False, None)
    token.cancel("interrupt")
    token.cancel("other")
    assert (token.cancelled, token.reason) == (True, "interrupt")
    plain = CancelToken()
    plain.cancel()
    assert (plain.cancelled, plain.reason) == (True, None)


def test_the_bridge_carries_the_reason_both_ways():
    token = CancelToken()
    token.cancel("interrupt")
    _, controller = bridge_cancel_token(token)
    assert controller.signal.reason == "interrupt"
    already = CancelToken()
    already.cancel()
    assert bridge_cancel_token(already)[1].signal.reason == "token_cancelled"
    fresh = CancelToken()
    _, live = bridge_cancel_token(fresh)
    live.abort("interrupt")
    assert (fresh.cancelled, fresh.reason) == (True, "interrupt")


def test_upstreams_interruption_messages():
    assert INTERRUPT_MESSAGE == "[Request interrupted by user]"
    assert INTERRUPT_MESSAGE_FOR_TOOL_USE == "[Request interrupted by user for tool use]"
    assert create_user_interruption_message() == _interruption(False)
    assert create_user_interruption_message(tool_use=True) == _interruption(True)


def test_abort_reason_prefers_the_token_then_the_turn():
    ctx = tool_context_for(AppStateStore())
    turn = AbortController()
    assert abort_reason(ctx, turn) is None
    turn.abort("interrupt")
    assert abort_reason(ctx, turn) == "interrupt"
    assert abort_reason(ctx) is None
    ctx.cancel.cancel("esc")
    assert abort_reason(ctx, turn) == "esc"
    unreasoned = AbortController()
    unreasoned.abort()
    assert abort_reason(tool_context_for(AppStateStore()), unreasoned) is None


# ── the command queue's subscribers ──────────────────────────────────────────

def test_subscribers_hear_every_change_and_only_changes():
    queue = CommandQueue()
    heard: list[int] = []
    unsubscribe = queue.subscribe(lambda: heard.append(len(queue)))
    queue.enqueue(QueuedCommand(value="a", mode="prompt"))
    queue.enqueue_pending_notification(QueuedCommand(value="b", mode="task-notification"))
    assert heard == [1, 2]
    queue.dequeue()
    assert heard == [1, 2, 1]
    assert queue.dequeue(lambda c: c.value == "zzz") is None
    assert queue.dequeue_all_matching(lambda c: c.value == "zzz") == []
    queue.remove([QueuedCommand(value="b", mode="task-notification")])  # not the same object
    assert heard == [1, 2, 1]
    queue.dequeue_all_matching(lambda c: True)
    assert heard == [1, 2, 1, 0]
    queue.enqueue(QueuedCommand(value="c", mode="prompt"))
    queue.remove(list(queue.snapshot()))
    assert heard == [1, 2, 1, 0, 1, 0]
    unsubscribe()
    unsubscribe()
    queue.enqueue(QueuedCommand(value="d", mode="prompt"))
    assert heard == [1, 2, 1, 0, 1, 0]


# ── the streaming executor ───────────────────────────────────────────────────

def _tool(name: str, behavior: Any = None) -> Any:
    async def call(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(content="ran")

    return build_tool(name=name, prompt="p", call=call, interrupt_behavior=behavior)


def _tracked(name: str) -> TrackedTool:
    return TrackedTool(id=name, tool_call={"id": name, "name": name, "args": {}},
                       assistant_message=AIMessage(content=""), status="executing", is_concurrency_safe=True)


def _raises() -> Any:
    raise RuntimeError("boom")


def test_an_interrupt_cancels_only_cancel_tools():
    ctx = tool_context_for(AppStateStore())
    executor = StreamingToolExecutor(tools=[_tool("C", lambda: "cancel"), _tool("B")], tool_context=ctx)
    executor.turn_abort_controller.abort("interrupt")
    assert executor.get_abort_reason(_tracked("C")) == "user_interrupted"
    assert executor.get_abort_reason(_tracked("B")) is None
    assert executor.get_abort_reason(_tracked("Unknown")) is None
    escaped = StreamingToolExecutor(tools=[_tool("B")], tool_context=tool_context_for(AppStateStore()))
    escaped.turn_abort_controller.abort("user-cancel")
    assert escaped.get_abort_reason(_tracked("B")) == "user_interrupted"
    token_ctx = tool_context_for(AppStateStore())
    token_ctx.cancel.cancel("interrupt")
    by_token = StreamingToolExecutor(tools=[_tool("B")], tool_context=token_ctx)
    assert by_token.get_abort_reason(_tracked("B")) is None


def test_the_interrupt_behavior_of_an_unknown_or_raising_tool_is_block():
    executor = StreamingToolExecutor(tools=[_tool("C", lambda: "cancel"), _tool("R", _raises)],
                                     tool_context=tool_context_for(AppStateStore()))
    assert executor.get_tool_interrupt_behavior(_tracked("C")) == "cancel"
    assert executor.get_tool_interrupt_behavior(_tracked("R")) == "block"
    assert executor.get_tool_interrupt_behavior(_tracked("X")) == "block"


def test_the_executor_reports_whether_every_executing_tool_is_interruptible():
    async def go() -> list[bool]:
        reports: list[bool] = []
        ctx = tool_context_for(AppStateStore())
        ctx.set_has_interruptible_tool_in_progress = reports.append
        release = asyncio.Event()

        async def slow(args: dict[str, Any], c: ToolContext) -> ToolResult:
            await release.wait()
            return ToolResult(content="slow")

        cancel_tool = build_tool(name="C", prompt="p", call=slow, interrupt_behavior=lambda: "cancel",
                                 is_concurrency_safe=lambda a: True)
        executor = StreamingToolExecutor(tools=[cancel_tool], tool_context=ctx)
        call: ToolCall = {"id": "c1", "name": "C", "args": {}}
        executor.add_tool(call, AIMessage(content="", tool_calls=[call]))
        await asyncio.sleep(0)
        assert reports == [True]
        release.set()
        async for _ in executor.get_remaining_results():
            pass
        return reports

    assert asyncio.run(go()) == [True, False]


def test_a_tool_aborted_before_it_starts_reports_too():
    async def go() -> list[bool]:
        reports: list[bool] = []
        ctx = tool_context_for(AppStateStore())
        ctx.set_has_interruptible_tool_in_progress = reports.append
        ctx.cancel.cancel()
        executor = StreamingToolExecutor(tools=[_tool("B")], tool_context=ctx)
        call: ToolCall = {"id": "c1", "name": "B", "args": {}}
        executor.add_tool(call, AIMessage(content="", tool_calls=[call]))
        results = [r async for r in executor.get_remaining_results()]
        assert isinstance(results[0], ToolMessage) and results[0].status == "error"
        return reports

    assert asyncio.run(go()) == [False, False]


def test_without_a_listener_nothing_is_reported():
    executor = StreamingToolExecutor(tools=[_tool("B")], tool_context=tool_context_for(AppStateStore()))
    executor.update_interruptible_state()  # no listener: nothing to call, nothing raised


def test_a_bubbled_tool_abort_carries_its_reason_to_the_run():
    async def go() -> CancelToken:
        ctx = tool_context_for(AppStateStore())

        async def rejecting(args: dict[str, Any], c: ToolContext) -> ToolResult:
            executor._tools[0].abort_controller.abort("permission_rejected")  # type: ignore[union-attr]
            return ToolResult(content="no")

        executor = StreamingToolExecutor(tools=[build_tool(name="T", prompt="p", call=rejecting)], tool_context=ctx)
        call: ToolCall = {"id": "c1", "name": "T", "args": {}}
        executor.add_tool(call, AIMessage(content="", tool_calls=[call]))
        async for _ in executor.get_remaining_results():
            pass
        return ctx.cancel

    token = asyncio.run(go())
    assert (token.cancelled, token.reason) == (True, "permission_rejected")


# ── the loop's interruption messages ─────────────────────────────────────────

def test_an_abort_after_the_model_repairs_the_calls_and_says_so():
    state = start()

    async def cancelling_model(s: LoopState, c: RunConfig) -> AsyncIterator[Message]:
        yield asks_for("RunTests", "c1")
        state.tool_context.cancel.cancel()

    events = collect(cancelling_model, state=state)
    repair, said, term = events[-3:]
    assert isinstance(repair, ToolMessage) and repair.tool_call_id == "c1"
    assert repair.content == "Interrupted by user"
    assert said == _interruption(False) and term == Terminated(reason=Terminal.ABORTED_STREAMING)


def test_a_submit_interrupt_after_the_model_skips_the_message():
    state = start()

    async def cancelling_model(s: LoopState, c: RunConfig) -> AsyncIterator[Message]:
        yield AIMessage(content="streaming")
        state.tool_context.cancel.cancel("interrupt")

    events = collect(cancelling_model, state=state)
    assert not any(e == _interruption(False) for e in events)
    assert events[-1] == Terminated(reason=Terminal.ABORTED_STREAMING)


def test_an_abort_during_tools_says_it_was_for_tool_use():
    for reason, expected in ((None, True), ("interrupt", False)):
        state = start()

        async def cancelling_tools(
            calls: tuple[ToolCall, ...], msgs: tuple[Message, ...], s: LoopState, c: RunConfig,
            state: LoopState = state, reason: Any = reason,
        ) -> AsyncIterator[Message]:
            yield ToolMessage(content="ran", tool_call_id=calls[0]["id"])
            state.tool_context.cancel.cancel(reason)

        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        events = collect(model, tools=cancelling_tools, state=state)
        assert events[-1] == Terminated(reason=Terminal.ABORTED_TOOLS)
        assert (events[-2] == _interruption(True)) is expected


# ── who aborts with 'interrupt' ──────────────────────────────────────────────

def test_a_now_priority_command_interrupts_a_running_turn_only():
    calls: list[Any] = []
    engine = SimpleNamespace(running=False, interrupt=calls.append)
    queue = CommandQueue()
    queue.enqueue(QueuedCommand(value="x", mode="prompt", priority="now"))
    interrupt_on_now_priority(engine, queue)  # type: ignore[arg-type]
    assert calls == []
    engine.running = True
    interrupt_on_now_priority(engine, queue)  # type: ignore[arg-type]
    assert calls == ["interrupt"]
    queue.dequeue()
    queue.enqueue(QueuedCommand(value="y", mode="prompt", priority="next"))
    interrupt_on_now_priority(engine, queue)  # type: ignore[arg-type]
    assert calls == ["interrupt"]


def test_the_engine_interrupts_with_a_reason_and_tracks_its_turn():
    from app.query.state import initial_loop_state
    from app.query_engine import QueryEngine
    from tests.test_loop_engine import CONFIG, make_fake_deps
    from tests.test_loop_query_engine import FakeTranscript

    ctx = tool_context_for(AppStateStore())
    seen: list[bool] = []

    async def model(s: LoopState, c: RunConfig) -> AsyncIterator[Message]:
        seen.append(engine.running)
        yield AIMessage(content="done")

    engine = QueryEngine(CONFIG, make_fake_deps(model), transcript=FakeTranscript(), session_id="s", context=ctx)
    assert engine.running is False

    async def go() -> None:
        async for _ in engine.submit(initial_loop_state((HumanMessage(content="hi"),), ctx)):
            pass

    asyncio.run(go())
    assert seen == [True] and engine.running is False
    engine.interrupt("interrupt")
    assert (ctx.cancel.cancelled, ctx.cancel.reason) == (True, "interrupt")


# ── Bash keeps a command running on 'interrupt' ──────────────────────────────

class _Shell:
    def __init__(self) -> None:
        self.result: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.killed = 0

    def kill(self) -> None:
        self.killed += 1
        if not self.result.done():
            self.result.set_result("killed")


def test_bash_does_not_kill_on_a_submit_interrupt():
    from app.tools.bash_tool.bash_tool import _wait_for_result

    async def go(reason: str | None) -> tuple[Any, int]:
        shell = _Shell()
        ctx = tool_context_for(AppStateStore())
        ctx.cancel.cancel(reason)

        def finish() -> None:
            if not shell.result.done():
                shell.result.set_result("done")

        asyncio.get_running_loop().call_later(0.15, finish)
        result = await _wait_for_result(shell, ctx, asyncio.Event(), lambda: None)
        return result, shell.killed

    assert asyncio.run(go("interrupt")) == ("done", 0)
    result, killed = asyncio.run(go(None))
    assert result == "killed" and killed >= 1


def test_the_repl_interrupts_a_turn_whose_tools_are_all_cancel_ones():
    from app.screens.repl import Repl

    calls: list[Any] = []
    queue = CommandQueue()
    ctx = SimpleNamespace(command_queue=queue, set_has_interruptible_tool_in_progress=None)
    run = SimpleNamespace(ctx=ctx, engine=SimpleNamespace(interrupt=calls.append), on_message=None)
    io = SimpleNamespace(write=lambda text: None)
    repl = Repl(run, io)  # type: ignore[arg-type]
    assert ctx.set_has_interruptible_tool_in_progress == repl._set_interruptible

    async def go() -> None:
        repl.turn = asyncio.get_running_loop().create_future()  # type: ignore[assignment]
        await repl.handle_line("first")
        ctx.set_has_interruptible_tool_in_progress(True)
        await repl.handle_line("second")
        repl.turn.set_result(None)  # type: ignore[union-attr]

    asyncio.run(go())
    assert calls == ["interrupt"]
    assert [c.value for c in queue.snapshot()] == ["first", "second"]
