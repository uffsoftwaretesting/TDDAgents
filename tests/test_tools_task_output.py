"""Phase 8: `TaskOutput` (`tools/TaskOutputTool/TaskOutputTool.tsx`)."""

from __future__ import annotations

import asyncio

import pytest

from app.state.app_state_store import AppStateStore
from app.tasks.local_agent_task.local_agent_task import (
    complete_agent_task,
    fail_agent_task,
    register_async_agent,
)
from app.tool import tool_context_for
from app.tools.task_output_tool.task_output_tool import (
    TASK_OUTPUT_TOOL_NAME,
    build_task_output_tool,
)


def _setup(tmp_path, transcript_text: str = "line one\n"):
    out = tmp_path / "a1.output"
    out.write_text(transcript_text)
    store = AppStateStore()
    register_async_agent(agent_id="a1", description="survey", prompt="look around", agent_type="Explore",
                         set_app_state=store.update, output_file=str(out))
    return store, tool_context_for(store), out


def _call(args, ctx):
    return asyncio.run(build_task_output_tool().call(args, ctx))


def test_the_tool_is_upstreams_shape():
    tool = build_task_output_tool()
    assert (tool.name, tool.aliases, TASK_OUTPUT_TOOL_NAME) == (
        "TaskOutput", ("AgentOutputTool", "BashOutputTool"), "TaskOutput")
    assert tool.input_schema == {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The task ID to get output from"},
            "block": {"type": "boolean", "default": True, "description": "Whether to wait for completion"},
            "timeout": {"type": "number", "minimum": 0, "maximum": 600000, "default": 30000,
                        "description": "Max wait time in ms"},
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }
    assert (tool.is_read_only({}), tool.is_concurrency_safe({}), tool.max_result_size_chars) == (True, True, 100_000)
    assert tool.description({}) == "[Deprecated] — prefer Read on the task output file path"
    assert tool.prompt.startswith("DEPRECATED: Prefer using the Read tool on the task's output file path instead.")
    assert tool.prompt.endswith("- Works with all task types: background shells, async agents, and remote sessions")


def test_validation(tmp_path):
    _store, ctx, _ = _setup(tmp_path)
    tool = build_task_output_tool()
    empty = tool.validate_input({"task_id": ""}, ctx)
    assert (empty.valid, empty.message, empty.error_code) == (False, "Task ID is required", 1)
    unknown = tool.validate_input({"task_id": "zz"}, ctx)
    assert (unknown.valid, unknown.message, unknown.error_code) == (False, "No task found with ID: zz", 2)
    assert tool.validate_input({"task_id": "a1"}, ctx).valid is True


def test_non_blocking_on_a_running_task_is_not_ready(tmp_path):
    store, ctx, _ = _setup(tmp_path)
    result = _call({"task_id": "a1", "block": False}, ctx)
    assert result.content == (
        "<retrieval_status>not_ready</retrieval_status>\n\n<task_id>a1</task_id>\n\n"
        "<task_type>local_agent</task_type>\n\n<status>running</status>\n\n<output>\nline one\n</output>"
    )
    assert store.get().tasks["a1"].notified is False


def test_non_blocking_on_a_finished_task_succeeds_and_marks_it_notified(tmp_path):
    store, ctx, _ = _setup(tmp_path)
    complete_agent_task("a1", "final answer", store.update)
    result = _call({"task_id": "a1", "block": False}, ctx)
    assert result.content.startswith("<retrieval_status>success</retrieval_status>")
    assert "<status>completed</status>" in result.content
    assert "<output>\nfinal answer\n</output>" in result.content
    assert store.get().tasks["a1"].notified is True


def test_blocking_waits_for_completion(tmp_path):
    store, ctx, _ = _setup(tmp_path)

    async def go():
        async def finish():
            await asyncio.sleep(0.15)
            complete_agent_task("a1", "done now", store.update)

        asyncio.ensure_future(finish())
        return await build_task_output_tool().call({"task_id": "a1"}, ctx)

    result = asyncio.run(go())
    assert "<retrieval_status>success</retrieval_status>" in result.content
    assert "done now" in result.content


def test_blocking_times_out_with_the_current_state(tmp_path):
    _store, ctx, _ = _setup(tmp_path)
    result = _call({"task_id": "a1", "timeout": 120}, ctx)
    assert result.content.startswith("<retrieval_status>timeout</retrieval_status>")
    assert "<status>running</status>" in result.content


def test_a_failed_task_reports_its_error(tmp_path):
    store, ctx, _ = _setup(tmp_path, "")
    fail_agent_task("a1", "provider down", store.update)
    result = _call({"task_id": "a1", "block": False}, ctx)
    assert "<status>failed</status>" in result.content
    assert "<error>provider down</error>" in result.content
    assert "<output>" not in result.content


def test_a_vanished_task_times_out_with_no_task(tmp_path):
    store, ctx, _ = _setup(tmp_path)

    async def go():
        async def vanish():
            await asyncio.sleep(0.05)
            store.update(lambda s: __import__("dataclasses").replace(s, tasks={}))

        asyncio.ensure_future(vanish())
        return await build_task_output_tool().call({"task_id": "a1", "timeout": 1000}, ctx)

    assert asyncio.run(go()).content == "<retrieval_status>timeout</retrieval_status>"


def test_a_cancelled_wait_aborts(tmp_path):
    from app.tools.agent_tool.agent_tool import AbortError

    _store, ctx, _ = _setup(tmp_path)
    ctx.cancel.cancel()
    with pytest.raises(AbortError):
        _call({"task_id": "a1"}, ctx)


def test_an_unknown_task_raises(tmp_path):
    with pytest.raises(ValueError, match="^No task found with ID: zz$"):
        _call({"task_id": "zz"}, tool_context_for(AppStateStore()))


def test_long_output_is_truncated_from_the_front(tmp_path, monkeypatch):
    monkeypatch.setenv("TASK_MAX_OUTPUT_LENGTH", "80")
    _store, ctx, out = _setup(tmp_path, "x" * 500 + "END")
    result = _call({"task_id": "a1", "block": False}, ctx)
    assert f"[Truncated. Full output: {out}]" in result.content and "END\n</output>" in result.content


# ── mutation-gate pins (Phase 8) ────────────────────────────────────────────

def _pending(store):
    import dataclasses

    store.update(lambda s: dataclasses.replace(
        s, tasks={**s.tasks, "a1": dataclasses.replace(s.tasks["a1"], status="pending")}))


def test_a_pending_task_is_not_ready_without_blocking(tmp_path):
    store, ctx, _ = _setup(tmp_path)
    _pending(store)
    result = _call({"task_id": "a1", "block": False}, ctx)
    assert result.content.startswith("<retrieval_status>not_ready</retrieval_status>")
    assert store.get().tasks["a1"].notified is False


def test_blocking_waits_through_pending_until_completion_and_marks_it_notified(tmp_path):
    store, ctx, _ = _setup(tmp_path)
    _pending(store)

    async def go():
        async def finish():
            await asyncio.sleep(0.15)
            import dataclasses

            store.update(lambda s: dataclasses.replace(
                s, tasks={**s.tasks, "a1": dataclasses.replace(s.tasks["a1"], status="running")}))
            complete_agent_task("a1", "done later", store.update)

        asyncio.ensure_future(finish())
        return await build_task_output_tool().call({"task_id": "a1", "timeout": 5000}, ctx)

    result = asyncio.run(go())
    assert result.content.startswith("<retrieval_status>success</retrieval_status>")
    assert store.get().tasks["a1"].notified is True


def test_a_pending_task_at_the_deadline_times_out(tmp_path):
    store, ctx, _ = _setup(tmp_path)
    _pending(store)
    result = _call({"task_id": "a1", "timeout": 0}, ctx)
    assert result.content.startswith("<retrieval_status>timeout</retrieval_status>")
    assert "<status>pending</status>" in result.content
    assert store.get().tasks["a1"].notified is False
