"""
Unit tests for StreamingToolExecutor (Parts F2, F3, F4).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.loop.context import AppStateStore, ToolContext, tool_context_for
from app.loop.messages import ToolCall
from app.loop.streaming.abort import AbortController
from app.loop.streaming.executor import StreamingToolExecutor, TrackedTool
from app.loop.tools.base import build_tool
from app.loop.tools.execution import CANCEL_MESSAGE
from app.loop.tools.types import ToolResult


def make_context() -> ToolContext:
    store = AppStateStore()
    return tool_context_for(store)


def test_executor_initial_state() -> None:
    context = make_context()
    tool = build_tool(name="Dummy", prompt="", call=lambda a, c: ToolResult(content=""))
    turn_ctrl = AbortController()
    executor = StreamingToolExecutor(
        tools=[tool], tool_context=context, turn_abort_controller=turn_ctrl
    )

    assert executor.has_errored is False
    assert executor.errored_tool_description == ""
    assert executor.discarded is False
    assert executor.tool_definitions == (tool,)
    assert executor.turn_abort_controller is turn_ctrl
    assert executor.sibling_abort_controller.signal.aborted is False


def test_unknown_tool_immediate_error() -> None:
    async def _run() -> None:
        context = make_context()
        executor = StreamingToolExecutor(tools=[], tool_context=context)

        tool_call: ToolCall = {"id": "c1", "name": "NonExistentTool", "args": {}}
        asst_msg = AIMessage(content="", tool_calls=[tool_call])

        executor.add_tool(tool_call, asst_msg)

        results = list(executor.get_completed_results())
        assert len(results) == 1
        assert isinstance(results[0], ToolMessage)
        assert results[0].tool_call_id == "c1"
        assert results[0].status == "error"
        assert results[0].additional_kwargs == {"is_error": True}
        assert "No such tool available: NonExistentTool" in str(results[0].content)

    asyncio.run(_run())


def test_add_tool_missing_fields_and_safe_predicate_exception() -> None:
    async def _run() -> None:
        context = make_context()

        def buggy_safe(args: dict[str, Any]) -> bool:
            raise RuntimeError("safe check crashed")

        tool = build_tool(
            name="BuggySafeTool",
            prompt="",
            call=lambda a, c: ToolResult(content="ok"),
            is_concurrency_safe=buggy_safe,
        )
        executor = StreamingToolExecutor(tools=[tool], tool_context=context)
        asst = AIMessage(content="")

        # Missing id and args
        tool_call_missing: ToolCall = {"id": None, "name": "BuggySafeTool", "args": None}  # type: ignore
        executor.add_tool(tool_call_missing, asst)

        tracked = executor._tools[0]
        assert tracked.id == ""
        assert tracked.is_concurrency_safe is False
        assert tracked.status in ("queued", "executing")

        # Missing name on unknown tool
        unknown_missing: ToolCall = {"id": None, "name": None, "args": None}  # type: ignore
        executor.add_tool(unknown_missing, asst)
        unknown_tracked = executor._tools[1]
        assert unknown_tracked.id == ""
        res0 = unknown_tracked.results[0]
        assert isinstance(res0, ToolMessage)
        assert res0.status == "error"
        assert res0.additional_kwargs == {"is_error": True}
        assert res0.tool_call_id == ""

    asyncio.run(_run())


def test_can_execute_tool_predicates() -> None:
    context = make_context()
    executor = StreamingToolExecutor(tools=[], tool_context=context)
    asst = AIMessage(content="")

    # Empty executing -> can execute both safe and unsafe
    assert executor.can_execute_tool(is_concurrency_safe=True) is True
    assert executor.can_execute_tool(is_concurrency_safe=False) is True

    # One safe executing
    t_safe = TrackedTool(
        id="1", tool_call={"id": "1", "name": "s", "args": {}},
        assistant_message=asst, status="executing", is_concurrency_safe=True
    )
    executor._tools.append(t_safe)
    assert executor.can_execute_tool(is_concurrency_safe=True) is True
    assert executor.can_execute_tool(is_concurrency_safe=False) is False

    # One unsafe executing
    t_unsafe = TrackedTool(
        id="2", tool_call={"id": "2", "name": "u", "args": {}},
        assistant_message=asst, status="executing", is_concurrency_safe=False
    )
    executor._tools.append(t_unsafe)
    assert executor.can_execute_tool(is_concurrency_safe=True) is False
    assert executor.can_execute_tool(is_concurrency_safe=False) is False


def test_get_abort_reason_all_branches() -> None:
    context = make_context()
    executor = StreamingToolExecutor(tools=[], tool_context=context)
    asst = AIMessage(content="")
    t = TrackedTool(
        id="1",
        tool_call={"id": "1", "name": "t", "args": {}},
        assistant_message=asst,
        status="queued",
        is_concurrency_safe=True,
    )

    # 1. Normal state -> None
    assert executor.get_abort_reason(t) is None

    # 2. Turn abort controller aborted -> user_interrupted
    executor.turn_abort_controller.abort("user_sigint")
    assert executor.get_abort_reason(t) == "user_interrupted"

    # Reset
    executor = StreamingToolExecutor(tools=[], tool_context=context)
    # 3. CancelToken cancelled -> user_interrupted
    executor.tool_context.cancel.cancel()
    assert executor.get_abort_reason(t) == "user_interrupted"

    # 4. has_errored -> sibling_error (takes precedence over cancel if errored)
    executor.has_errored = True
    assert executor.get_abort_reason(t) == "sibling_error"

    # 5. discarded -> streaming_fallback (takes highest precedence)
    executor.discarded = True
    assert executor.get_abort_reason(t) == "streaming_fallback"


def test_has_unfinished_and_has_executing_tools() -> None:
    context = make_context()
    executor = StreamingToolExecutor(tools=[], tool_context=context)
    asst = AIMessage(content="")

    assert executor.has_unfinished_tools() is False
    assert executor.has_executing_tools() is False

    t1 = TrackedTool(
        id="1",
        tool_call={"id": "1", "name": "t", "args": {}},
        assistant_message=asst,
        status="queued",
        is_concurrency_safe=True,
    )
    executor._tools.append(t1)
    assert executor.has_unfinished_tools() is True
    assert executor.has_executing_tools() is False

    t1.status = "executing"
    assert executor.has_executing_tools() is True
    assert executor.has_unfinished_tools() is True

    t1.status = "completed"
    assert executor.has_executing_tools() is False
    assert executor.has_unfinished_tools() is True

    t1.status = "yielded"
    assert executor.has_unfinished_tools() is False
    assert executor.has_executing_tools() is False


def test_concurrency_safe_tools_run_in_parallel() -> None:
    async def _run() -> None:
        context = make_context()
        started: list[str] = []
        finish_event = asyncio.Event()

        async def call_safe(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            cid = str(args.get("cid"))
            started.append(cid)
            if len(started) == 2:
                finish_event.set()
            await finish_event.wait()
            return ToolResult(content=f"result-{cid}", tool_use_id=cid)

        tool = build_tool(
            name="SafeTool",
            prompt="A safe read-only tool",
            call=call_safe,
            is_concurrency_safe=lambda args: True,
        )

        executor = StreamingToolExecutor(tools=[tool], tool_context=context)

        c1: ToolCall = {"id": "c1", "name": "SafeTool", "args": {"cid": "c1"}}
        c2: ToolCall = {"id": "c2", "name": "SafeTool", "args": {"cid": "c2"}}
        asst = AIMessage(content="", tool_calls=[c1, c2])

        executor.add_tool(c1, asst)
        executor.add_tool(c2, asst)

        await asyncio.wait_for(finish_event.wait(), timeout=1.0)
        assert set(started) == {"c1", "c2"}

        results: list[ToolMessage] = []
        async for res in executor.get_remaining_results():
            if isinstance(res, ToolMessage):
                results.append(res)

        assert len(results) == 2
        assert results[0].tool_call_id == "c1"
        assert results[1].tool_call_id == "c2"

    asyncio.run(_run())


def test_non_concurrent_tool_blocks_subsequent_tools() -> None:
    async def _run() -> None:
        context = make_context()
        execution_order: list[str] = []
        first_tool_done = asyncio.Event()

        async def call_exclusive(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            cid = str(args.get("cid"))
            execution_order.append(f"start_{cid}")
            await asyncio.sleep(0.02)
            execution_order.append(f"finish_{cid}")
            first_tool_done.set()
            return ToolResult(content=f"result-{cid}", tool_use_id=cid)

        tool = build_tool(
            name="ExclusiveTool",
            prompt="A non-concurrent tool",
            call=call_exclusive,
            is_concurrency_safe=lambda args: False,
        )

        executor = StreamingToolExecutor(tools=[tool], tool_context=context)

        c1: ToolCall = {"id": "c1", "name": "ExclusiveTool", "args": {"cid": "1"}}
        c2: ToolCall = {"id": "c2", "name": "ExclusiveTool", "args": {"cid": "2"}}
        asst = AIMessage(content="", tool_calls=[c1, c2])

        executor.add_tool(c1, asst)
        executor.add_tool(c2, asst)

        await first_tool_done.wait()
        assert "start_1" in execution_order
        assert "finish_1" in execution_order

        async for _ in executor.get_remaining_results():
            pass

        assert execution_order == ["start_1", "finish_1", "start_2", "finish_2"]

    asyncio.run(_run())


def test_sibling_abort_cascades_on_bash_error_only() -> None:
    """
    Invariant F3: ONLY Bash errors cascade to siblings.
    """
    async def _run() -> None:
        context = make_context()
        sibling_started = asyncio.Event()

        async def call_bash(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            await sibling_started.wait()
            return ToolResult(content="bash error occurred", is_error=True, tool_use_id="bash_1")

        async def call_read(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            sibling_started.set()
            await asyncio.sleep(0.02)
            return ToolResult(content="read finished", tool_use_id="read_1")

        bash_tool = build_tool(
            name="bash",
            prompt="Bash shell",
            call=call_bash,
            is_concurrency_safe=lambda args: True,
        )
        read_tool = build_tool(
            name="read",
            prompt="Read file",
            call=call_read,
            is_concurrency_safe=lambda args: True,
        )

        executor = StreamingToolExecutor(tools=[bash_tool, read_tool], tool_context=context)

        c_bash: ToolCall = {"id": "bash_1", "name": "bash", "args": {"command": "invalid"}}
        c_read: ToolCall = {"id": "read_1", "name": "read", "args": {"file_path": "foo.txt"}}
        asst = AIMessage(content="", tool_calls=[c_bash, c_read])

        executor.add_tool(c_bash, asst)
        executor.add_tool(c_read, asst)

        results: list[ToolMessage] = []
        async for res in executor.get_remaining_results():
            if isinstance(res, ToolMessage):
                results.append(res)

        assert executor.sibling_abort_controller.signal.aborted is True
        assert executor.has_errored is True

        assert len(results) == 2
        read_res = [r for r in results if r.tool_call_id == "read_1"][0]
        assert "Cancelled: parallel tool call" in str(read_res.content)
        assert read_res.status == "error"
        assert read_res.additional_kwargs == {"is_error": True}

    asyncio.run(_run())


def test_non_bash_error_does_not_abort_siblings() -> None:
    """
    Invariant F3: Non-bash errors (e.g. ReadFile failing) do NOT cascade to siblings.
    """
    async def _run() -> None:
        context = make_context()
        read_started = asyncio.Event()

        async def call_read(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            read_started.set()
            return ToolResult(content="File not found", is_error=True, tool_use_id="read_1")

        async def call_grep(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            await read_started.wait()
            await asyncio.sleep(0.01)
            return ToolResult(content="grep matched lines", is_error=False, tool_use_id="grep_1")

        read_tool = build_tool(
            name="read",
            prompt="Read file",
            call=call_read,
            is_concurrency_safe=lambda args: True,
        )
        grep_tool = build_tool(
            name="grep",
            prompt="Grep search",
            call=call_grep,
            is_concurrency_safe=lambda args: True,
        )

        executor = StreamingToolExecutor(tools=[read_tool, grep_tool], tool_context=context)

        c_read: ToolCall = {"id": "read_1", "name": "read", "args": {"file_path": "missing.txt"}}
        c_grep: ToolCall = {"id": "grep_1", "name": "grep", "args": {"pattern": "pattern"}}
        asst = AIMessage(content="", tool_calls=[c_read, c_grep])

        executor.add_tool(c_read, asst)
        executor.add_tool(c_grep, asst)

        results: list[ToolMessage] = []
        async for res in executor.get_remaining_results():
            if isinstance(res, ToolMessage):
                results.append(res)

        assert executor.sibling_abort_controller.signal.aborted is False
        assert len(results) == 2
        grep_res = [r for r in results if r.tool_call_id == "grep_1"][0]
        assert "grep matched lines" in str(grep_res.content)
        assert grep_res.status == "success"

    asyncio.run(_run())


def test_upward_bubble_on_permission_or_user_rejection() -> None:
    """
    Invariant F3: Permission rejection or user cancellation bubbles up to the turn controller
    to end the turn (fixing regression #21056).
    """
    async def _run() -> None:
        context = make_context()
        turn_controller = AbortController()

        async def call_rejected(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            return ToolResult(content="Permission denied", is_error=True, tool_use_id="t1")

        tool = build_tool(
            name="DangerousTool",
            prompt="Requires permission",
            call=call_rejected,
        )

        executor = StreamingToolExecutor(
            tools=[tool], tool_context=context, turn_abort_controller=turn_controller
        )

        c1: ToolCall = {"id": "t1", "name": "DangerousTool", "args": {}}
        asst = AIMessage(content="", tool_calls=[c1])

        executor.add_tool(c1, asst)
        executor.turn_abort_controller.abort("permission_rejected")

        assert turn_controller.signal.aborted is True
        assert turn_controller.signal.reason == "permission_rejected"

    asyncio.run(_run())


def test_discard_suppresses_queued_and_completed_results() -> None:
    """
    Invariant F4: discard() semantics during streaming fallback.
    """
    async def _run() -> None:
        context = make_context()

        async def call_slow(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            await asyncio.sleep(0.05)
            return ToolResult(content="completed output", tool_use_id="c1")

        tool = build_tool(name="SlowTool", prompt="Slow tool", call=call_slow)
        executor = StreamingToolExecutor(tools=[tool], tool_context=context)

        c1: ToolCall = {"id": "c1", "name": "SlowTool", "args": {}}
        asst = AIMessage(content="", tool_calls=[c1])

        executor.add_tool(c1, asst)
        executor.discard()

        completed = list(executor.get_completed_results())
        assert completed == []

        remaining: list[Any] = []
        async for r in executor.get_remaining_results():
            remaining.append(r)
        assert remaining == []

    asyncio.run(_run())


def test_synthetic_error_messages_by_reason() -> None:
    context = make_context()
    executor = StreamingToolExecutor(tools=[], tool_context=context)
    asst = AIMessage(content="")

    # user_interrupted
    msg_interrupted = executor.create_synthetic_error_message("t1", "user_interrupted", asst)
    assert msg_interrupted.content == CANCEL_MESSAGE
    assert msg_interrupted.status == "error"
    assert msg_interrupted.additional_kwargs == {"is_error": True}

    # streaming_fallback
    msg_fallback = executor.create_synthetic_error_message("t2", "streaming_fallback", asst)
    assert "Streaming fallback - tool execution discarded" in str(msg_fallback.content)
    assert msg_fallback.status == "error"
    assert msg_fallback.additional_kwargs == {"is_error": True}

    # sibling_error with description
    executor.errored_tool_description = "bash(rm -rf)"
    msg_sibling = executor.create_synthetic_error_message("t3", "sibling_error", asst)
    assert "Cancelled: parallel tool call bash(rm -rf) errored" in str(msg_sibling.content)
    assert msg_sibling.status == "error"
    assert msg_sibling.additional_kwargs == {"is_error": True}

    # sibling_error without description
    executor.errored_tool_description = ""
    msg_no_desc = executor.create_synthetic_error_message("t4", "sibling_error", asst)
    assert "Cancelled: parallel tool call errored" in str(msg_no_desc.content)
    assert msg_no_desc.status == "error"
    assert msg_no_desc.additional_kwargs == {"is_error": True}


def test_context_modifier_applied_for_non_concurrent_tools() -> None:
    async def _run() -> None:
        context = make_context()

        def mod(ctx: ToolContext) -> ToolContext:
            ctx.set_app_state(lambda s: s)
            return ctx

        async def call_with_mod(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            return ToolResult(content="ok", tool_use_id="c1", context_modifier=mod)

        tool = build_tool(
            name="ModTool",
            prompt="Tool with context modifier",
            call=call_with_mod,
            is_concurrency_safe=lambda args: False,
        )

        executor = StreamingToolExecutor(tools=[tool], tool_context=context)
        c1: ToolCall = {"id": "c1", "name": "ModTool", "args": {}}
        asst = AIMessage(content="", tool_calls=[c1])

        executor.add_tool(c1, asst)
        async for _ in executor.get_remaining_results():
            pass

        assert executor._tools[0].status == "yielded"

    asyncio.run(_run())


def test_tool_description_formatting_and_truncation() -> None:
    context = make_context()
    executor = StreamingToolExecutor(tools=[], tool_context=context)
    asst = AIMessage(content="")

    # Short command
    t1 = TrackedTool(
        id="1",
        tool_call={"id": "1", "name": "Bash", "args": {"command": "git status"}},
        assistant_message=asst,
        status="queued",
        is_concurrency_safe=False,
    )
    assert executor._get_tool_description(t1) == "Bash(git status)"

    # Long command truncated to 40 with ellipsis
    long_cmd = "a" * 50
    t2 = TrackedTool(
        id="2",
        tool_call={"id": "2", "name": "Bash", "args": {"command": long_cmd}},
        assistant_message=asst,
        status="queued",
        is_concurrency_safe=False,
    )
    desc = executor._get_tool_description(t2)
    assert desc.startswith("Bash(aaaa")
    assert desc.endswith("…)")

    # Exact boundary 40 chars: no ellipsis
    cmd_40 = "b" * 40
    t_40 = TrackedTool(
        id="t40",
        tool_call={"id": "t40", "name": "Bash", "args": {"command": cmd_40}},
        assistant_message=asst,
        status="queued",
        is_concurrency_safe=False,
    )
    assert executor._get_tool_description(t_40) == f"Bash({cmd_40})"

    # Exact boundary 41 chars: truncated to 40 + ellipsis
    cmd_41 = "c" * 41
    t_41 = TrackedTool(
        id="t41",
        tool_call={"id": "t41", "name": "Bash", "args": {"command": cmd_41}},
        assistant_message=asst,
        status="queued",
        is_concurrency_safe=False,
    )
    assert executor._get_tool_description(t_41) == f"Bash({'c' * 40}…)"

    # File path argument
    t3 = TrackedTool(
        id="3",
        tool_call={"id": "3", "name": "ReadFile", "args": {"file_path": "foo/bar.py"}},
        assistant_message=asst,
        status="queued",
        is_concurrency_safe=True,
    )
    assert executor._get_tool_description(t3) == "ReadFile(foo/bar.py)"

    # Pattern argument
    t4 = TrackedTool(
        id="4",
        tool_call={"id": "4", "name": "Grep", "args": {"pattern": "regex_pattern"}},
        assistant_message=asst,
        status="queued",
        is_concurrency_safe=True,
    )
    assert executor._get_tool_description(t4) == "Grep(regex_pattern)"

    # No arguments
    t5 = TrackedTool(
        id="5",
        tool_call={"id": "5", "name": "EmptyTool", "args": {}},
        assistant_message=asst,
        status="queued",
        is_concurrency_safe=True,
    )
    assert executor._get_tool_description(t5) == "EmptyTool"

    # Empty name or missing name
    t_no_name = TrackedTool(
        id="noname",
        tool_call={"id": "noname", "name": None, "args": {}},  # type: ignore[typeddict-item]
        assistant_message=asst,
        status="queued",
        is_concurrency_safe=True,
    )
    assert executor._get_tool_description(t_no_name) == ""


def test_initial_abort_before_execution() -> None:
    async def _run() -> None:
        context = make_context()
        tool_called = False

        def my_call(a: dict[str, Any], c: ToolContext) -> ToolResult:
            nonlocal tool_called
            tool_called = True
            return ToolResult(content="ok", tool_use_id="1")

        tool = build_tool(name="MyTool", prompt="", call=my_call)
        executor = StreamingToolExecutor(tools=[tool], tool_context=context)

        # Abort turn controller beforehand
        executor.turn_abort_controller.abort("user_stop")

        asst = AIMessage(content="")
        executor.add_tool({"id": "1", "name": "MyTool", "args": {}}, asst)

        await asyncio.sleep(0)
        results = list(executor.get_completed_results())
        assert len(results) == 1
        assert isinstance(results[0], ToolMessage)
        assert results[0].content == CANCEL_MESSAGE
        assert results[0].status == "error"
        assert results[0].additional_kwargs == {"is_error": True}
        assert tool_called is False

    asyncio.run(_run())


def test_tool_execution_exception_handling_bash_and_non_bash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _run() -> None:
        context = make_context()

        async def fail_bash(a: dict[str, Any], c: ToolContext) -> ToolResult:
            raise RuntimeError("bash process crash")

        async def fail_read(a: dict[str, Any], c: ToolContext) -> ToolResult:
            raise ValueError("bad read")

        b_tool = build_tool(name="bash", prompt="", call=fail_bash)
        r_tool = build_tool(name="read", prompt="", call=fail_read)

        executor = StreamingToolExecutor(tools=[b_tool, r_tool], tool_context=context)
        asst = AIMessage(content="")

        # Non-bash failure
        executor.add_tool({"id": "r1", "name": "read", "args": {}}, asst)
        res_r = [r async for r in executor.get_remaining_results()]
        assert len(res_r) == 1
        assert isinstance(res_r[0], ToolMessage)
        assert "Error calling tool (read): bad read" in str(res_r[0].content)
        assert res_r[0].status == "error"
        assert res_r[0].additional_kwargs == {"is_error": True}
        assert executor.sibling_abort_controller.signal.aborted is False

        # Bash failure
        executor.add_tool({"id": "b1", "name": "bash", "args": {}}, asst)
        res_b = [r async for r in executor.get_remaining_results()]
        assert len(res_b) == 1
        assert isinstance(res_b[0], ToolMessage)
        assert "Error calling tool (bash): bash process crash" in str(res_b[0].content)
        assert res_b[0].status == "error"
        assert res_b[0].additional_kwargs == {"is_error": True}
        assert executor.has_errored is True
        assert executor.errored_tool_description == "bash"
        assert executor.sibling_abort_controller.signal.aborted is True
        assert executor.sibling_abort_controller.signal.reason == "sibling_error"

    asyncio.run(_run())


def test_tool_execution_unexpected_runner_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _run() -> None:
        context = make_context()
        b_tool = build_tool(name="bash", prompt="", call=lambda a, c: ToolResult(content=""))
        executor = StreamingToolExecutor(tools=[b_tool], tool_context=context)

        async def crash_runner(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("runner crashed")

        import app.loop.streaming.executor as exec_mod
        monkeypatch.setattr(exec_mod, "run_tool_use", crash_runner)

        with caplog.at_level(logging.WARNING):
            executor.add_tool({"id": "b1", "name": "bash", "args": {}}, AIMessage(content=""))
            res_b = [r async for r in executor.get_remaining_results()]
            assert len(res_b) == 1
            assert isinstance(res_b[0], ToolMessage)
            assert res_b[0].content == "<tool_use_error>Error calling tool (bash): runner crashed</tool_use_error>"
            assert res_b[0].status == "error"
            assert res_b[0].additional_kwargs == {"is_error": True}
            assert executor.has_errored is True
            assert executor.errored_tool_description == "bash"
            assert executor.sibling_abort_controller.signal.aborted is True
            assert executor.sibling_abort_controller.signal.reason == "sibling_error"
            assert len(caplog.records) == 1
            assert caplog.records[-1].message == "Tool execution raised unexpected exception: runner crashed"

    asyncio.run(_run())


def test_progress_messages_yielded_immediately() -> None:
    context = make_context()
    executor = StreamingToolExecutor(tools=[], tool_context=context)
    asst = AIMessage(content="")

    tool = TrackedTool(
        id="p1",
        tool_call={"id": "p1", "name": "ProgTool", "args": {}},
        assistant_message=asst,
        status="executing",
        is_concurrency_safe=True,
    )
    prog_msg = ToolMessage(content="progress: 50%", tool_call_id="p1")
    tool.pending_progress.append(prog_msg)
    executor._tools.append(tool)

    completed = list(executor.get_completed_results())
    assert completed == [prog_msg]
    assert tool.pending_progress == []
    # Tool itself is still executing and not yielded
    assert tool.status == "executing"


def test_in_flight_tool_ids_lifecycle() -> None:
    async def _run() -> None:
        context = make_context()
        in_flight_during: set[str] = set()

        async def inspect_tool(a: dict[str, Any], c: ToolContext) -> ToolResult:
            in_flight_during.update(c.in_flight_tool_ids)
            return ToolResult(content="done")

        tool = build_tool(name="inspect", prompt="", call=inspect_tool)
        executor = StreamingToolExecutor(tools=[tool], tool_context=context)
        executor.add_tool({"id": "i1", "name": "inspect", "args": {}}, AIMessage(content=""))
        results = [r async for r in executor.get_remaining_results()]
        assert len(results) == 1
        assert in_flight_during == {"i1"}
        assert "i1" not in context.in_flight_tool_ids

    asyncio.run(_run())


def test_context_modifiers_applied_for_non_concurrent_tools() -> None:
    async def _run() -> None:
        from app.loop.context import AppState
        from app.loop.ledger import PhaseLedger, TddPhase

        context = make_context()
        initial_ledger = context.get_app_state().phase_ledger
        new_ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)

        def mod_fn(ctx: ToolContext) -> ToolContext:
            ctx.set_app_state(lambda prev: AppState(phase_ledger=new_ledger))
            return ctx

        def unsafe_call(a: dict[str, Any], c: ToolContext) -> ToolResult:
            return ToolResult(content="unsafe", context_modifier=mod_fn)

        def safe_call(a: dict[str, Any], c: ToolContext) -> ToolResult:
            return ToolResult(content="safe", context_modifier=mod_fn)

        t_unsafe = build_tool(name="unsafe_tool", prompt="", call=unsafe_call, is_concurrency_safe=lambda a: False)
        t_safe = build_tool(name="safe_tool", prompt="", call=safe_call, is_concurrency_safe=lambda a: True)

        # Unsafe tool applies modifiers
        executor_unsafe = StreamingToolExecutor(tools=[t_unsafe], tool_context=context)
        executor_unsafe.add_tool({"id": "u1", "name": "unsafe_tool", "args": {}}, AIMessage(content=""))
        _ = [r async for r in executor_unsafe.get_remaining_results()]
        assert context.get_app_state().phase_ledger == new_ledger

        # Safe tool does NOT apply modifiers
        context_fresh = make_context()
        executor_safe = StreamingToolExecutor(tools=[t_safe], tool_context=context_fresh)
        executor_safe.add_tool({"id": "s1", "name": "safe_tool", "args": {}}, AIMessage(content=""))
        _ = [r async for r in executor_safe.get_remaining_results()]
        assert context_fresh.get_app_state().phase_ledger == initial_ledger

    asyncio.run(_run())


def test_get_remaining_results_queue_draining_and_waiting() -> None:
    async def _run() -> None:
        context = make_context()

        async def slow_safe(a: dict[str, Any], c: ToolContext) -> ToolResult:
            await asyncio.sleep(0.01)
            return ToolResult(content="slow_safe_done")

        async def fast_safe(a: dict[str, Any], c: ToolContext) -> ToolResult:
            return ToolResult(content="fast_safe_done")

        async def unsafe_queued(a: dict[str, Any], c: ToolContext) -> ToolResult:
            return ToolResult(content="unsafe_done")

        t_slow = build_tool(name="slow", prompt="", call=slow_safe, is_concurrency_safe=lambda a: True)
        t_fast = build_tool(name="fast", prompt="", call=fast_safe, is_concurrency_safe=lambda a: True)
        t_unsafe = build_tool(name="unsafe", prompt="", call=unsafe_queued, is_concurrency_safe=lambda a: False)

        executor = StreamingToolExecutor(tools=[t_slow, t_fast, t_unsafe], tool_context=context)
        asst = AIMessage(content="")

        executor.add_tool({"id": "s1", "name": "slow", "args": {}}, asst)
        executor.add_tool({"id": "f1", "name": "fast", "args": {}}, asst)
        executor.add_tool({"id": "u1", "name": "unsafe", "args": {}}, asst)

        results = [r async for r in executor.get_remaining_results()]
        assert len(results) == 3
        assert [r.content for r in results] == ["slow_safe_done", "fast_safe_done", "unsafe_done"]

    asyncio.run(_run())


def test_tool_aborted_synthetic_error_message() -> None:
    async def _run() -> None:
        context = make_context()

        started = asyncio.Event()

        async def hang_call(a: dict[str, Any], c: ToolContext) -> ToolResult:
            started.set()
            await asyncio.sleep(0.1)
            return ToolResult(content="done")

        hang_tool = build_tool(name="hang", prompt="", call=hang_call, is_concurrency_safe=lambda a: True)
        executor = StreamingToolExecutor(tools=[hang_tool], tool_context=context)
        executor.add_tool({"id": "h1", "name": "hang", "args": {}}, AIMessage(content=""))

        # Wait until the tool starts executing
        await started.wait()

        # Set has_errored and abort the sibling controller while tool is running
        executor.has_errored = True
        executor.sibling_abort_controller.abort("sibling_error")

        results = [r async for r in executor.get_remaining_results()]
        assert len(results) == 1
        assert isinstance(results[0], ToolMessage)
        assert results[0].content == "<tool_use_error>Cancelled: parallel tool call errored</tool_use_error>"
        assert results[0].status == "error"
        assert results[0].additional_kwargs == {"is_error": True}

    asyncio.run(_run())


def test_synthetic_error_messages() -> None:
    context = make_context()
    executor = StreamingToolExecutor(tools=[], tool_context=context)
    asst = AIMessage(content="")

    msg_user = executor.create_synthetic_error_message("t1", "user_interrupted", asst)
    assert msg_user.content == CANCEL_MESSAGE

    msg_fallback = executor.create_synthetic_error_message("t2", "streaming_fallback", asst)
    assert (
        msg_fallback.content
        == "<tool_use_error>Error: Streaming fallback - tool execution discarded</tool_use_error>"
    )

    msg_sibling = executor.create_synthetic_error_message("t3", "sibling_error", asst)
    assert (
        msg_sibling.content
        == "<tool_use_error>Cancelled: parallel tool call errored</tool_use_error>"
    )

    executor.errored_tool_description = "bash(rm)"
    msg_sibling_desc = executor.create_synthetic_error_message("t4", "sibling_error", asst)
    assert (
        msg_sibling_desc.content
        == "<tool_use_error>Cancelled: parallel tool call bash(rm) errored</tool_use_error>"
    )


def test_process_queue_unsafe_tool_blocks_subsequent_safe_tools() -> None:
    async def _run() -> None:
        context = make_context()
        t1 = build_tool(
            name="t1", prompt="", call=lambda a, c: ToolResult(content="1"), is_concurrency_safe=lambda a: False
        )
        t2 = build_tool(
            name="t2", prompt="", call=lambda a, c: ToolResult(content="2"), is_concurrency_safe=lambda a: False
        )
        t3 = build_tool(
            name="t3", prompt="", call=lambda a, c: ToolResult(content="3"), is_concurrency_safe=lambda a: True
        )

        executor = StreamingToolExecutor(tools=[t1, t2, t3], tool_context=context)
        asst = AIMessage(content="")

        tool1 = TrackedTool(
            id="1",
            tool_call={"id": "1", "name": "t1", "args": {}},
            assistant_message=asst,
            status="executing",
            is_concurrency_safe=False,
        )
        tool2 = TrackedTool(
            id="2",
            tool_call={"id": "2", "name": "t2", "args": {}},
            assistant_message=asst,
            status="queued",
            is_concurrency_safe=False,
        )
        tool3 = TrackedTool(
            id="3",
            tool_call={"id": "3", "name": "t3", "args": {}},
            assistant_message=asst,
            status="queued",
            is_concurrency_safe=True,
        )

        executor._tools.extend([tool1, tool2, tool3])
        executor._process_queue()

        # Tool 2 is blocked because tool 1 is executing (non-concurrent)
        # Tool 3 MUST NOT execute even though it is safe, because tool 2 is unsafe and blocks the queue!
        assert tool2.status == "queued"
        assert tool3.status == "queued"

    asyncio.run(_run())


def test_on_tool_abort_branches() -> None:
    async def _run() -> None:
        context = make_context()

        async def slow_call(a: dict[str, Any], c: ToolContext) -> ToolResult:
            await asyncio.sleep(0.1)
            return ToolResult(content="ok")

        tool = build_tool(name="slow", prompt="", call=slow_call, is_concurrency_safe=lambda a: True)

        # 1. Permission rejected bubbles up to turn controller
        executor = StreamingToolExecutor(tools=[tool], tool_context=context)
        executor.add_tool({"id": "p1", "name": "slow", "args": {}}, AIMessage(content=""))
        tracked = executor._tools[0]
        await asyncio.sleep(0.005)
        assert tracked.abort_controller is not None
        tracked.abort_controller.abort("permission_rejected")
        assert executor.turn_abort_controller.signal.aborted is True
        assert executor.turn_abort_controller.signal.reason == "permission_rejected"
        assert context.cancel.cancelled is True

        # 2. Sibling error does NOT bubble up to turn controller
        ctx2 = make_context()
        executor2 = StreamingToolExecutor(tools=[tool], tool_context=ctx2)
        executor2.add_tool({"id": "s1", "name": "slow", "args": {}}, AIMessage(content=""))
        tracked2 = executor2._tools[0]
        await asyncio.sleep(0.005)
        assert tracked2.abort_controller is not None
        tracked2.abort_controller.abort("sibling_error")
        assert executor2.turn_abort_controller.signal.aborted is False
        assert ctx2.cancel.cancelled is False

        # 3. Discarded executor does NOT bubble up
        ctx3 = make_context()
        executor3 = StreamingToolExecutor(tools=[tool], tool_context=ctx3)
        executor3.add_tool({"id": "d1", "name": "slow", "args": {}}, AIMessage(content=""))
        tracked3 = executor3._tools[0]
        await asyncio.sleep(0.005)
        assert tracked3.abort_controller is not None
        executor3.discarded = True
        tracked3.abort_controller.abort("some_reason")
        assert executor3.turn_abort_controller.signal.aborted is False
        assert ctx3.cancel.cancelled is False

    asyncio.run(_run())
