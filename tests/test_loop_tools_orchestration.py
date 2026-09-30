"""
Tests for partition_tool_calls, run_tools, and context-modifier replay (Parts B5 & B6).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from langchain_core.messages import AIMessage, ToolMessage

from app.loop.config import Gates, RunConfig
from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.messages import ToolCall
from app.loop.state import initial_loop_state
from app.loop.tools.base import build_tool
from app.loop.tools.execution import CANCEL_MESSAGE
from app.loop.tools.orchestration import (
    ToolBatch,
    apply_context_modifier,
    partition_tool_calls,
    run_tools,
)
from app.loop.tools.types import ToolResult


def make_config() -> RunConfig:
    return RunConfig(
        run_id="test_run",
        gates=Gates(web_tools_available=False, postgres_checkpointing=False),
    )


class TestPartitionToolCalls:
    """Part B5: partition_tool_calls."""

    def test_partitioning_consecutive_safe_and_serial_tools(self):
        safe_tool = build_tool(
            name="safe_tool",
            prompt="p",
            call=lambda a, c: ToolResult(content="ok"),
            is_concurrency_safe=lambda a: True,
        )
        serial_tool = build_tool(
            name="serial_tool",
            prompt="p",
            call=lambda a, c: ToolResult(content="ok"),
            is_concurrency_safe=lambda a: False,
        )
        tools = [safe_tool, serial_tool]

        calls: list[ToolCall] = [
            {"name": "safe_tool", "args": {}, "id": "c1"},
            {"name": "safe_tool", "args": {}, "id": "c2"},
            {"name": "serial_tool", "args": {}, "id": "c3"},
            {"name": "safe_tool", "args": {}, "id": "c4"},
            {"name": "serial_tool", "args": {}, "id": "c5"},
        ]

        batches = partition_tool_calls(calls, tools)
        assert len(batches) == 4
        # Batch 1: safe c1, c2
        assert batches[0] == ToolBatch(is_concurrency_safe=True, calls=(calls[0], calls[1]))
        # Batch 2: serial c3
        assert batches[1] == ToolBatch(is_concurrency_safe=False, calls=(calls[2],))
        # Batch 3: safe c4
        assert batches[2] == ToolBatch(is_concurrency_safe=True, calls=(calls[3],))
        # Batch 4: serial c5
        assert batches[3] == ToolBatch(is_concurrency_safe=False, calls=(calls[4],))

    def test_partitioning_reads_concurrency_safe_from_args(self):
        tool = build_tool(
            name="arg_tool",
            prompt="p",
            call=lambda a, c: ToolResult(content="ok"),
            is_concurrency_safe=lambda a: bool(a.get("safe", False)),
        )
        calls: list[ToolCall] = [
            {"name": "arg_tool", "args": {"safe": True}, "id": "c1"},
            {"name": "arg_tool", "args": {"safe": False}, "id": "c2"},
            {"name": "arg_tool", "args": None, "id": "c3"},  # type: ignore[typeddict-item]
            {"name": None, "args": {}, "id": "c4"},  # type: ignore[typeddict-item]
        ]
        batches = partition_tool_calls(calls, [tool])
        assert len(batches) == 4
        assert batches[0].is_concurrency_safe is True
        assert batches[0].calls[0]["args"] == {"safe": True}
        assert batches[1].is_concurrency_safe is False
        assert batches[2].is_concurrency_safe is False
        assert batches[3].is_concurrency_safe is False

    def test_unknown_tool_treated_as_not_concurrency_safe(self):
        calls: list[ToolCall] = [{"name": "unknown_tool", "args": {}, "id": "c1"}]
        batches = partition_tool_calls(calls, [])
        assert len(batches) == 1
        assert batches[0].is_concurrency_safe is False

    def test_exploding_concurrency_safe_predicate_fail_closed(self):
        def explode(args):
            raise RuntimeError("quote parse failure")

        tool = build_tool(
            name="unsafe",
            prompt="p",
            call=lambda a, c: ToolResult(content="ok"),
            is_concurrency_safe=explode,
        )
        calls: list[ToolCall] = [{"name": "unsafe", "args": {}, "id": "c1"}]
        batches = partition_tool_calls(calls, [tool])
        assert len(batches) == 1
        assert batches[0].is_concurrency_safe is False


class TestApplyContextModifier:
    """Part B6: apply_context_modifier logic."""

    def test_same_context_no_copy(self):
        ctx = tool_context_for(AppStateStore())
        ret = apply_context_modifier(ctx, lambda c: c)
        assert ret is ctx

    def test_apply_context_modifier_full_replacement(self):
        ctx = tool_context_for(AppStateStore())
        new_store = AppStateStore()
        new_state = AppState()
        new_store.update(lambda s: new_state)

        new_tool = build_tool(name="new_tool", prompt="p", call=lambda a, c: ToolResult(content="new"))
        new_ctx = tool_context_for(new_store, tools=(new_tool,))
        new_ctx.cancel.cancel()
        new_ctx.messages = (AIMessage(content="new_msg"),)
        new_ctx.in_flight_tool_ids = {"mod_id"}

        applied = apply_context_modifier(ctx, lambda c: new_ctx)
        assert applied is ctx
        assert ctx.cancel.cancelled is True
        assert ctx.messages == (AIMessage(content="new_msg"),)
        assert ctx.in_flight_tool_ids == {"mod_id"}
        assert ctx.get_app_state() is new_state
        dummy_state = AppState()
        ctx.set_app_state(lambda s: dummy_state)
        assert new_store.get() is dummy_state
        assert ctx.tools == (new_tool,)

    def test_apply_context_modifier_without_tools_attr(self):
        from types import SimpleNamespace

        ctx = SimpleNamespace(
            cancel=None, get_app_state=None, set_app_state=None, messages=(), in_flight_tool_ids=set()
        )
        new_ctx = SimpleNamespace(
            cancel="c", get_app_state="g", set_app_state="s", messages=("m",), in_flight_tool_ids={"i"}
        )
        apply_context_modifier(ctx, lambda c: new_ctx)  # type: ignore[arg-type,return-value]
        assert ctx.cancel == "c"
        assert not hasattr(ctx, "tools")


class TestRunTools:
    """Part B5 & B6: run_tools execution and context replay."""

    def test_serial_tool_execution_and_in_flight_tracking(self):
        async def go():
            in_flight_during_call: list[set[str]] = []

            async def do_serial_1(args, ctx):
                in_flight_during_call.append(set(ctx.in_flight_tool_ids))
                return ToolResult(content="res1")

            async def do_serial_2(args, ctx):
                in_flight_during_call.append(set(ctx.in_flight_tool_ids))
                return ToolResult(content="res2")

            t1 = build_tool(name="t1", prompt="p", call=do_serial_1, is_concurrency_safe=lambda a: False)
            t2 = build_tool(name="t2", prompt="p", call=do_serial_2, is_concurrency_safe=lambda a: False)
            tools = [t1, t2]

            ctx = tool_context_for(AppStateStore())
            state = initial_loop_state((), ctx)
            config = make_config()

            calls: tuple[ToolCall, ...] = (
                {"name": "t1", "args": {}, "id": "c1"},
                {"name": "t2", "args": {}, "id": "c2"},
            )
            asst_msgs: tuple[AIMessage, ...] = (AIMessage(content="run"),)

            results: list[ToolMessage] = []
            async for msg in run_tools(calls, asst_msgs, state, config, tools=tools):
                assert isinstance(msg, ToolMessage)
                results.append(msg)

            assert len(results) == 2
            assert results[0].content == "res1"
            assert results[0].tool_call_id == "c1"
            assert results[1].content == "res2"
            assert results[1].tool_call_id == "c2"

            # Check that in_flight_tool_ids tracked each call individually and is empty at end
            assert in_flight_during_call == [{"c1"}, {"c2"}]
            assert ctx.in_flight_tool_ids == set()

        asyncio.run(go())

    def test_concurrent_tool_execution(self):
        async def go():
            in_flight_during_call: list[set[str]] = []

            async def do_concurrent(args, ctx):
                await asyncio.sleep(0.01)
                in_flight_during_call.append(set(ctx.in_flight_tool_ids))
                return ToolResult(content=f"ran {args['id']}")

            tool = build_tool(name="safe_tool", prompt="p", call=do_concurrent, is_concurrency_safe=lambda a: True)
            ctx = tool_context_for(AppStateStore())
            state = initial_loop_state((), ctx)
            config = make_config()

            calls: tuple[ToolCall, ...] = (
                {"name": "safe_tool", "args": {"id": "1"}, "id": "c1"},
                {"name": "safe_tool", "args": {"id": "2"}, "id": "c2"},
            )
            asst_msgs: tuple[AIMessage, ...] = (AIMessage(content="run"),)

            results: list[ToolMessage] = []
            async for msg in run_tools(calls, asst_msgs, state, config, tools=[tool]):
                assert isinstance(msg, ToolMessage)
                results.append(msg)

            assert len(results) == 2
            # Both were in-flight concurrently
            assert any({"c1", "c2"}.issubset(seen) for seen in in_flight_during_call)
            assert ctx.in_flight_tool_ids == set()

        asyncio.run(go())

    def test_context_modifier_replay_in_model_call_order(self):
        """
        Part B6: Even if concurrent tool B completes before tool A,
        their context modifications are replayed in the model's call order (A then B).
        """
        async def go():
            replay_order: list[str] = []

            def mod_a(ctx):
                replay_order.append("A")
                ctx.messages = ctx.messages + (AIMessage(content="from_A"),)
                return ctx

            def mod_b(ctx):
                replay_order.append("B")
                ctx.messages = ctx.messages + (AIMessage(content="from_B"),)
                return ctx

            # Tool A takes longer to finish (0.03s), but was called FIRST by the model.
            async def call_a(args, ctx):
                await asyncio.sleep(0.03)
                return ToolResult(content="res_a", context_modifier=mod_a)

            # Tool B finishes faster (0.01s), but was called SECOND by the model.
            async def call_b(args, ctx):
                await asyncio.sleep(0.01)
                return ToolResult(content="res_b", context_modifier=mod_b)

            tool_a = build_tool(name="tool_a", prompt="p", call=call_a, is_concurrency_safe=lambda a: True)
            tool_b = build_tool(name="tool_b", prompt="p", call=call_b, is_concurrency_safe=lambda a: True)
            tools = [tool_a, tool_b]

            ctx = tool_context_for(AppStateStore())
            state = initial_loop_state((), ctx)
            config = make_config()

            calls: tuple[ToolCall, ...] = (
                {"name": "tool_a", "args": {}, "id": "c_a"},
                {"name": "tool_b", "args": {}, "id": "c_b"},
            )
            asst_msgs: tuple[AIMessage, ...] = (AIMessage(content="run"),)

            async for _ in run_tools(calls, asst_msgs, state, config, tools=tools):
                pass

            # Invariant: context modifiers MUST be replayed in model's call order: ["A", "B"]
            assert replay_order == ["A", "B"]
            assert [m.content for m in ctx.messages] == ["from_A", "from_B"]

        asyncio.run(go())

    def test_context_modifier_new_instance_updates_in_place(self):
        """When modifier returns a replaced ToolContext instance, in-place update succeeds."""
        async def go():
            def mod(ctx):
                return replace(ctx, in_flight_tool_ids={"modified"})

            async def call_fn(args, ctx):
                return ToolResult(content="ok", context_modifier=mod)

            tool = build_tool(name="mod_tool", prompt="p", call=call_fn)
            ctx = tool_context_for(AppStateStore())
            state = initial_loop_state((), ctx)
            config = make_config()

            calls: tuple[ToolCall, ...] = ({"name": "mod_tool", "args": {}, "id": "c1"},)
            asst_msgs: tuple[AIMessage, ...] = (AIMessage(content="run"),)

            async for _ in run_tools(calls, asst_msgs, state, config, tools=[tool]):
                pass

            assert ctx.in_flight_tool_ids == {"modified"}

        asyncio.run(go())

    def test_serial_tool_execution_with_context_modifier(self):
        async def go():
            def mod(ctx):
                ctx.messages = ctx.messages + (AIMessage(content="from_serial_mod"),)
                return ctx

            async def do_serial(args, ctx):
                return ToolResult(content="serial_done", context_modifier=mod)

            tool = build_tool(name="serial_mod", prompt="p", call=do_serial, is_concurrency_safe=lambda a: False)
            ctx = tool_context_for(AppStateStore())
            state = initial_loop_state((), ctx)
            config = make_config()

            calls: tuple[ToolCall, ...] = ({"name": "serial_mod", "args": {}, "id": "c1"},)
            results = []
            async for msg in run_tools(calls, (AIMessage(content="start"),), state, config, tools=[tool]):
                results.append(msg)

            assert len(results) == 1
            assert results[0].content == "serial_done"
            assert [m.content for m in ctx.messages] == ["from_serial_mod"]

        asyncio.run(go())

    def test_run_tools_history_repair_on_cancellation(self):
        """
        Part B7: When cancelled midway through tool calls,
        yield_missing_tool_results repairs the history for remaining tools.
        """
        async def go():
            ctx = tool_context_for(AppStateStore())

            async def cancel_midway(args, c):
                ctx.cancel.cancel()
                return ToolResult(content="first finished")

            t1 = build_tool(
                name="t1", prompt="p", call=cancel_midway, is_concurrency_safe=lambda a: False
            )
            t2 = build_tool(
                name="t2", prompt="p", call=lambda a, c: ToolResult(content="t2"), is_concurrency_safe=lambda a: False
            )
            tools = [t1, t2]

            state = initial_loop_state((), ctx)
            config = make_config()

            calls: tuple[ToolCall, ...] = (
                {"name": "t1", "args": {}, "id": "c1"},
                {"name": "t2", "args": {}, "id": "c2"},
            )
            asst_msgs: tuple[AIMessage, ...] = (AIMessage(content="run"),)

            results: list[ToolMessage] = []
            async for msg in run_tools(calls, asst_msgs, state, config, tools=tools):
                assert isinstance(msg, ToolMessage)
                results.append(msg)

            # Invariant: exactly 2 messages emitted (t1 result + synthetic repair for t2)
            assert len(results) == 2
            assert results[0].tool_call_id == "c1"
            assert results[0].content == "first finished"

            assert results[1].tool_call_id == "c2"
            assert results[1].content == CANCEL_MESSAGE
            assert results[1].status == "error"
            assert results[1].additional_kwargs.get("synthetic_repair") is True

        asyncio.run(go())

    def test_run_tools_uses_tools_from_context_when_omitted(self):
        async def go():
            tool = build_tool(name="context_tool", prompt="p", call=lambda a, c: ToolResult(content="from_context"))
            ctx = tool_context_for(AppStateStore(), tools=(tool,))
            state = initial_loop_state((), ctx)
            config = make_config()

            calls: tuple[ToolCall, ...] = ({"name": "context_tool", "args": {}, "id": "c1"},)
            asst_msgs: tuple[AIMessage, ...] = (AIMessage(content="run"),)

            results: list[ToolMessage] = []
            async for msg in run_tools(calls, asst_msgs, state, config):
                assert isinstance(msg, ToolMessage)
                results.append(msg)

            assert len(results) == 1
            assert results[0].content == "from_context"

        asyncio.run(go())

    def test_run_tools_empty_call_id_concurrent_and_serial(self):
        async def go():
            safe_tool = build_tool(
                name="safe",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok_safe"),
                is_concurrency_safe=lambda a: True,
            )
            serial_tool = build_tool(
                name="serial",
                prompt="p",
                call=lambda a, c: ToolResult(content="ok_serial"),
                is_concurrency_safe=lambda a: False,
            )
            ctx = tool_context_for(AppStateStore())
            state = initial_loop_state((), ctx)
            config = make_config()

            calls: tuple[ToolCall, ...] = (
                {"name": "safe", "args": {}, "id": ""},
                {"name": "serial", "args": {}, "id": ""},
            )
            results: list[ToolMessage] = []
            async for msg in run_tools(calls, (), state, config, tools=[safe_tool, serial_tool]):
                assert isinstance(msg, ToolMessage)
                results.append(msg)

            assert len(results) == 2
            assert results[0].tool_call_id == ""
            assert results[0].content == "ok_safe"
            assert results[1].tool_call_id == ""
            assert results[1].content == "ok_serial"

        asyncio.run(go())

    def test_run_tools_generator_exit_clean_shutdown(self):
        async def go():
            t1 = build_tool(
                name="t1", prompt="p", call=lambda a, c: ToolResult(content="1"), is_concurrency_safe=lambda a: False
            )
            t2 = build_tool(
                name="t2", prompt="p", call=lambda a, c: ToolResult(content="2"), is_concurrency_safe=lambda a: False
            )
            ctx = tool_context_for(AppStateStore())
            state = initial_loop_state((), ctx)
            config = make_config()
            calls: tuple[ToolCall, ...] = (
                {"name": "t1", "args": {}, "id": "c1"},
                {"name": "t2", "args": {}, "id": "c2"},
            )

            # Break after first yielded message
            async for _ in run_tools(calls, (), state, config, tools=[t1, t2]):
                break

        asyncio.run(go())

    def test_run_tools_already_cancelled_yields_all_synthetic_cancels(self):
        async def go():
            tool = build_tool(name="tool", prompt="p", call=lambda a, c: ToolResult(content="ok"))
            ctx = tool_context_for(AppStateStore())
            ctx.cancel.cancel()
            state = initial_loop_state((), ctx)
            config = make_config()

            calls: tuple[ToolCall, ...] = (
                {"name": "tool", "args": {}, "id": "c1"},
                {"name": "tool", "args": {}, "id": "c2"},
            )
            results: list[ToolMessage] = []
            async for msg in run_tools(calls, (), state, config, tools=[tool]):
                assert isinstance(msg, ToolMessage)
                results.append(msg)

            assert len(results) == 2
            assert results[0].tool_call_id == "c1"
            assert results[0].content == CANCEL_MESSAGE
            assert results[0].additional_kwargs.get("synthetic_repair") is True
            assert results[1].tool_call_id == "c2"
            assert results[1].content == CANCEL_MESSAGE
            assert results[1].additional_kwargs.get("synthetic_repair") is True

        asyncio.run(go())
