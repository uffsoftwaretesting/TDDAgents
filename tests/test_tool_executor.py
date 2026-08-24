"""
Tool-call partitioning and batch execution.

Deliberately tested against purpose-built fake tools rather than the real roster: what is
under test is the scheduling rule, and a fake can be made to block, crash, or record its
own timing on demand in ways a real tool cannot.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.config.config import Config
from app.tools.base import Capability, ToolResult, execute_tool
from app.tools.executor import (
    Batch,
    ToolCall,
    execute_tool_calls,
    partition_tool_calls,
)
from app.tools.registry import ToolRegistry
from tests.conftest import EchoArgs, make_read_tool, make_tool


def reader(name: str = "Reader"):
    """Read-only and concurrency-safe: batches with its neighbours."""
    return make_read_tool(name)


def writer(name: str = "WriteFile"):
    """Neither read-only nor concurrency-safe: always runs alone."""
    return make_tool(name, required_capability=lambda args: Capability.WRITE)


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry([reader("ReadFile"), reader("Grep"), writer("WriteFile"), writer("Bash")])


def calls(*names: str) -> list[ToolCall]:
    return [ToolCall(name=name, args={"text": name}, id=f"id{i}", index=i)
            for i, name in enumerate(names)]


def shapes(batches: list[Batch]) -> list[tuple[bool, list[str]]]:
    return [(b.is_concurrency_safe, [c.name for c in b.calls]) for b in batches]


class TestPartitioning:
    def test_consecutive_safe_calls_form_one_batch(self, registry):
        result = partition_tool_calls(calls("ReadFile", "Grep", "ReadFile"), registry)
        assert shapes(result) == [(True, ["ReadFile", "Grep", "ReadFile"])]

    def test_each_unsafe_call_is_its_own_batch(self, registry):
        result = partition_tool_calls(calls("WriteFile", "Bash"), registry)
        assert shapes(result) == [(False, ["WriteFile"]), (False, ["Bash"])]

    def test_a_write_splits_the_reads_around_it(self, registry):
        """Order is preserved: the reads after the write may well depend on it."""
        result = partition_tool_calls(
            calls("ReadFile", "Grep", "WriteFile", "ReadFile", "Grep"), registry
        )
        assert shapes(result) == [
            (True, ["ReadFile", "Grep"]),
            (False, ["WriteFile"]),
            (True, ["ReadFile", "Grep"]),
        ]

    def test_empty_input(self, registry):
        assert partition_tool_calls([], registry) == []

    def test_single_safe_call(self, registry):
        assert shapes(partition_tool_calls(calls("ReadFile"), registry)) == [(True, ["ReadFile"])]

    def test_unknown_tool_is_never_batched(self, registry):
        result = partition_tool_calls(calls("ReadFile", "Nope", "Grep"), registry)
        assert shapes(result) == [(True, ["ReadFile"]), (False, ["Nope"]), (True, ["Grep"])]

    def test_unparseable_arguments_make_a_call_unsafe(self, registry):
        """If the arguments do not parse, the predicate was never consulted — fail closed."""
        bad = [ToolCall(name="ReadFile", args={"text": {"not": "a string"}}, index=0)]
        assert shapes(partition_tool_calls(bad, registry)) == [(False, ["ReadFile"])]

    def test_a_throwing_predicate_makes_a_call_unsafe(self):
        def _boom(args):
            raise ValueError("shell-quote parse failure")

        registry = ToolRegistry([make_read_tool("Risky", is_concurrency_safe=_boom)])
        assert shapes(partition_tool_calls(calls("Risky"), registry)) == [(False, ["Risky"])]

    def test_safety_is_decided_per_input(self):
        """Bash("ls") batches; Bash("rm -rf") does not. Only a per-input predicate can say."""
        registry = ToolRegistry(
            [make_tool("Bash", is_concurrency_safe=lambda args: args.text.startswith("ls"))]
        )
        batch = partition_tool_calls(
            [
                ToolCall("Bash", {"text": "ls a"}, index=0),
                ToolCall("Bash", {"text": "ls b"}, index=1),
                ToolCall("Bash", {"text": "rm -rf"}, index=2),
            ],
            registry,
        )
        assert shapes(batch) == [(True, ["Bash", "Bash"]), (False, ["Bash"])]


class TestExecutionOrder:
    def test_results_come_back_in_call_order(self, registry, tool_ctx):
        results = execute_tool_calls(calls("ReadFile", "Grep", "WriteFile"), registry, tool_ctx)
        assert [r.call_index for r in results] == [0, 1, 2]
        assert [r.tool_name for r in results] == ["ReadFile", "Grep", "WriteFile"]

    def test_call_ids_survive(self, registry, tool_ctx):
        results = execute_tool_calls(calls("ReadFile", "Grep"), registry, tool_ctx)
        assert [r.call_id for r in results] == ["id0", "id1"]

    def test_indices_are_assigned_when_absent(self, registry, tool_ctx):
        raw = [ToolCall(name="ReadFile"), ToolCall(name="Grep"), ToolCall(name="ReadFile")]
        results = execute_tool_calls(raw, registry, tool_ctx)
        assert [r.call_index for r in results] == [0, 1, 2]

    def test_order_holds_when_completion_order_is_reversed(self, tool_ctx):
        """
        The point of the reassembly. The slow call finishes last but must still come first.
        """
        def _slow(args, ctx):
            if args.text == "slow":
                time.sleep(0.15)
            return ToolResult(tool_name="Reader", content=args.text)

        registry = ToolRegistry([make_read_tool("Reader", call=_slow)])
        raw = [
            ToolCall("Reader", {"text": "slow"}, index=0),
            ToolCall("Reader", {"text": "fast"}, index=1),
        ]
        results = execute_tool_calls(raw, registry, tool_ctx)
        assert [r.content for r in results] == ["slow", "fast"]

    def test_empty_call_list(self, registry, tool_ctx):
        assert execute_tool_calls([], registry, tool_ctx) == []

    def test_every_call_gets_exactly_one_result(self, registry, tool_ctx):
        """A dangling tool call is rejected by the provider outright."""
        requested = calls("ReadFile", "WriteFile", "Grep", "Nope")
        results = execute_tool_calls(requested, registry, tool_ctx)
        assert len(results) == len(requested)
        assert [r.call_index for r in results] == [0, 1, 2, 3]


class TestParallelism:
    def test_a_safe_batch_actually_runs_concurrently(self, tool_ctx):
        barrier = threading.Barrier(3, timeout=5)

        def _wait(args, ctx):
            barrier.wait()  # deadlocks and raises unless all three run at once
            return ToolResult(tool_name="Reader", content="ok")

        registry = ToolRegistry([make_read_tool("Reader", call=_wait)])
        results = execute_tool_calls(calls("Reader", "Reader", "Reader"), registry, tool_ctx)
        assert [r.is_error for r in results] == [False, False, False]

    def test_unsafe_calls_never_overlap(self, tool_ctx):
        overlaps = []
        active = []
        lock = threading.Lock()

        def _record(args, ctx):
            with lock:
                active.append(1)
                overlaps.append(len(active))
            time.sleep(0.02)
            with lock:
                active.pop()
            return ToolResult(tool_name="WriteFile", content="ok")

        registry = ToolRegistry(
            [make_tool("WriteFile", call=_record, required_capability=lambda a: Capability.WRITE)]
        )
        execute_tool_calls(calls("WriteFile", "WriteFile", "WriteFile"), registry, tool_ctx)
        assert max(overlaps) == 1

    def test_concurrency_is_capped(self, tool_ctx, monkeypatch):
        monkeypatch.setattr(Config, "MAX_TOOL_CONCURRENCY", 2)
        peak = []
        active = []
        lock = threading.Lock()

        def _record(args, ctx):
            with lock:
                active.append(1)
                peak.append(len(active))
            time.sleep(0.02)
            with lock:
                active.pop()
            return ToolResult(tool_name="Reader", content="ok")

        registry = ToolRegistry([make_read_tool("Reader", call=_record)])
        execute_tool_calls(calls(*(["Reader"] * 6)), registry, tool_ctx)
        assert max(peak) <= 2

    def test_concurrent_oversized_results_get_distinct_paths(self, tool_ctx, fake_sandbox):
        """The result counter is shared across a batch; without a lock two Greps collide."""
        registry = ToolRegistry(
            [
                make_read_tool(
                    "Grep",
                    max_result_chars=100,
                    call=lambda args, ctx: ToolResult(tool_name="Grep", content="z" * 5_000),
                )
            ]
        )
        results = execute_tool_calls(calls(*(["Grep"] * 8)), registry, tool_ctx)
        paths = [r.persisted_path for r in results]
        assert len(set(paths)) == 8
        assert len(fake_sandbox.files) == 8


class TestErrorContainment:
    def test_one_failure_does_not_stop_its_siblings(self, tool_ctx):
        def _maybe_boom(args, ctx):
            if args.text == "bad":
                raise RuntimeError("boom")
            return ToolResult(tool_name="Reader", content=args.text)

        registry = ToolRegistry([make_read_tool("Reader", call=_maybe_boom)])
        raw = [
            ToolCall("Reader", {"text": "good1"}, index=0),
            ToolCall("Reader", {"text": "bad"}, index=1),
            ToolCall("Reader", {"text": "good2"}, index=2),
        ]
        results = execute_tool_calls(raw, registry, tool_ctx)

        assert [r.is_error for r in results] == [False, True, False]
        assert results[0].content == "good1"
        assert results[2].content == "good2"

    def test_one_failure_does_not_stop_later_batches(self, registry, tool_ctx):
        def _boom(args, ctx):
            raise RuntimeError("boom")

        broken = ToolRegistry([make_tool("Bad", call=_boom), reader("Reader")])
        results = execute_tool_calls(calls("Bad", "Reader"), broken, tool_ctx)
        assert results[0].is_error is True
        assert results[1].is_error is False

    def test_unknown_tool_reports_what_is_available(self, registry, tool_ctx):
        result = execute_tool_calls(calls("Nope"), registry, tool_ctx)[0]
        assert result.is_error is True
        assert "Unknown tool 'Nope'" in result.content
        assert "ReadFile" in result.content

    def test_unknown_tool_keeps_its_position(self, registry, tool_ctx):
        results = execute_tool_calls(calls("ReadFile", "Nope"), registry, tool_ctx)
        assert results[1].call_index == 1
        assert results[1].tool_name == "Nope"


class TestCancellation:
    def test_calls_after_a_cancel_are_answered_not_dropped(self, registry, tool_ctx):
        tool_ctx.cancel_token.cancel()
        results = execute_tool_calls(calls("WriteFile", "Bash"), registry, tool_ctx)
        assert len(results) == 2
        assert all(r.is_error for r in results)
        assert all("Cancelled" in r.content for r in results)

    def test_cancelling_midway_stops_the_remaining_batches(self, tool_ctx):
        def _cancel_after_first(args, ctx):
            ctx.cancel_token.cancel()
            return ToolResult(tool_name="WriteFile", content="ran")

        registry = ToolRegistry(
            [
                make_tool(
                    "WriteFile",
                    call=_cancel_after_first,
                    required_capability=lambda a: Capability.WRITE,
                )
            ]
        )
        results = execute_tool_calls(calls("WriteFile", "WriteFile", "WriteFile"), registry, tool_ctx)

        assert results[0].content == "ran"
        assert results[1].content == "Cancelled before execution."
        assert results[2].content == "Cancelled before execution."

    def test_an_uncancelled_run_is_unaffected(self, registry, tool_ctx):
        results = execute_tool_calls(calls("ReadFile", "WriteFile"), registry, tool_ctx)
        assert not any("Cancelled" in r.content for r in results)


class TestGatesStillApply:
    """The executor must not become a way around `execute_tool`."""

    def test_the_capability_gate_holds_inside_a_batch(self, registry, make_tool_ctx):
        ctx = make_tool_ctx(permission_mode="read_only")
        results = execute_tool_calls(calls("ReadFile", "WriteFile"), registry, ctx)
        assert results[0].is_error is False
        assert results[1].is_error is True

    def test_results_are_governed_inside_a_batch(self, tool_ctx):
        registry = ToolRegistry(
            [
                make_read_tool(
                    "Grep",
                    max_result_chars=100,
                    call=lambda args, ctx: ToolResult(tool_name="Grep", content="z" * 5_000),
                )
            ]
        )
        result = execute_tool_calls(calls("Grep"), registry, tool_ctx)[0]
        assert result.truncated is True

    def test_execute_tool_and_the_executor_agree(self, tool_ctx, registry):
        """One call through each path must produce the same result."""
        direct = execute_tool(registry.get("ReadFile"), {"text": "x"}, tool_ctx)
        batched = execute_tool_calls([ToolCall("ReadFile", {"text": "x"})], registry, tool_ctx)[0]
        assert direct.content == batched.content
        assert direct.is_error == batched.is_error


class TestToolCallDefaults:
    def test_defaults(self):
        call = ToolCall(name="ReadFile")
        assert call.args == {}
        assert call.id == ""
        assert call.index == 0

    def test_args_are_not_shared_between_instances(self):
        first, second = ToolCall(name="A"), ToolCall(name="B")
        first.args["x"] = 1
        assert second.args == {}

    def test_schema_is_reachable_for_partitioning(self):
        assert EchoArgs.model_validate({"text": "x"}).text == "x"


class TestPlaceholderResultsAreFullyFormed:
    """
    An unknown-tool or cancelled placeholder still has to line up with its call. Losing the
    index or the id puts the wrong result next to the wrong request in the message list.
    """

    def test_unknown_tool_keeps_index_and_id(self, registry, tool_ctx):
        raw = [ToolCall("Nope", {}, id="call-42", index=3)]
        result = execute_tool_calls(raw, registry, tool_ctx)[0]
        assert result.call_index == 3
        assert result.call_id == "call-42"
        assert result.tool_name == "Nope"

    def test_cancelled_keeps_index_and_id(self, registry, tool_ctx):
        tool_ctx.cancel_token.cancel()
        raw = [ToolCall("WriteFile", {}, id="call-7", index=5)]
        result = execute_tool_calls(raw, registry, tool_ctx)[0]
        assert result.call_index == 5
        assert result.call_id == "call-7"
        assert result.tool_name == "WriteFile"

    def test_mixed_placeholders_stay_aligned_with_their_calls(self, registry, tool_ctx):
        raw = [
            ToolCall("ReadFile", {"text": "a"}, id="i0", index=0),
            ToolCall("Nope", {}, id="i1", index=1),
            ToolCall("Grep", {"text": "b"}, id="i2", index=2),
        ]
        results = execute_tool_calls(raw, registry, tool_ctx)
        assert [(r.call_index, r.call_id, r.tool_name) for r in results] == [
            (0, "i0", "ReadFile"),
            (1, "i1", "Nope"),
            (2, "i2", "Grep"),
        ]


class TestBatchOfTwo:
    def test_exactly_two_safe_calls_still_run_in_parallel(self, tool_ctx):
        """Two is the smallest batch worth parallelizing; an off-by-one would serialize it."""
        barrier = threading.Barrier(2, timeout=5)

        def _wait(args, ctx):
            barrier.wait()
            return ToolResult(tool_name="Reader", content="ok")

        registry = ToolRegistry([make_read_tool("Reader", call=_wait)])
        results = execute_tool_calls(calls("Reader", "Reader"), registry, tool_ctx)
        assert [r.is_error for r in results] == [False, False]

    def test_unknown_tool_lists_available_tools_comma_separated(self, registry, tool_ctx):
        result = execute_tool_calls(calls("Nope"), registry, tool_ctx)[0]
        assert "Bash, Grep, ReadFile, WriteFile" in result.content
