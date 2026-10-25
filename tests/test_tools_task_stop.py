"""Phase 8: `TaskStop` (`tools/TaskStopTool/TaskStopTool.ts`) and `tasks/stopTask.ts`."""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

from app.loop.context import AppStateStore, tool_context_for
from app.loop.tasks.local_agent_task import register_async_agent
from app.loop.tasks.stop_task import StopTaskError, stop_task
from app.loop.tasks.task import create_task_state_base
from app.loop.tools.task_stop import TASK_STOP_TOOL_NAME, build_task_stop_tool


def _store_with_agent() -> tuple[AppStateStore, object]:
    store = AppStateStore()
    task = register_async_agent(agent_id="a1", description="survey", prompt="p", agent_type="Explore",
                                set_app_state=store.update, output_file="/o")
    return store, task


def test_stop_task_kills_a_running_agent():
    store, task = _store_with_agent()
    result = asyncio.run(stop_task("a1", store.get, store.update))
    assert result == ("a1", "local_agent", "survey")
    assert store.get().tasks["a1"].status == "killed"
    assert task.cancel.cancelled is True  # type: ignore[attr-defined]


def test_stop_task_errors_carry_upstreams_codes():
    store, _ = _store_with_agent()
    with pytest.raises(StopTaskError, match="^No task found with ID: nope$") as missing:
        asyncio.run(stop_task("nope", store.get, store.update))
    assert missing.value.code == "not_found"
    asyncio.run(stop_task("a1", store.get, store.update))
    with pytest.raises(StopTaskError, match=r"^Task a1 is not running \(status: killed\)$") as stopped:
        asyncio.run(stop_task("a1", store.get, store.update))
    assert stopped.value.code == "not_running"
    shell = dataclasses.replace(create_task_state_base("b1", "local_bash", "ls", "/o"), status="running")
    store.update(lambda s: dataclasses.replace(s, tasks={**s.tasks, "b1": shell}))
    with pytest.raises(StopTaskError, match="^Unsupported task type: local_bash$") as unsupported:
        asyncio.run(stop_task("b1", store.get, store.update))
    assert unsupported.value.code == "unsupported_type"


def test_the_tool_is_upstreams_shape():
    tool = build_task_stop_tool()
    assert (tool.name, tool.aliases, TASK_STOP_TOOL_NAME) == ("TaskStop", ("KillShell",), "TaskStop")
    assert tool.input_schema == {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The ID of the background task to stop"},
            "shell_id": {"type": "string", "description": "Deprecated: use task_id instead"},
        },
        "additionalProperties": False,
    }
    assert tool.is_concurrency_safe({}) is True
    assert tool.max_result_size_chars == 100_000
    assert tool.prompt == (
        "\n- Stops a running background task by its ID\n- Takes a task_id parameter identifying the task to "
        "stop\n- Returns a success or failure status\n- Use this tool when you need to terminate a "
        "long-running task\n"
    )
    assert tool.description({}) == "Stop a running background task by ID"


def _validate(tool, args, store):
    return tool.validate_input(args, tool_context_for(store))


def test_validation_names_what_is_wrong():
    store, _ = _store_with_agent()
    tool = build_task_stop_tool()
    missing = _validate(tool, {}, store)
    assert (missing.valid, missing.message, missing.error_code) == (False, "Missing required parameter: task_id", 1)
    unknown = _validate(tool, {"task_id": "zz"}, store)
    assert (unknown.valid, unknown.message, unknown.error_code) == (False, "No task found with ID: zz", 1)
    assert _validate(tool, {"shell_id": "a1"}, store).valid is True
    asyncio.run(stop_task("a1", store.get, store.update))
    done = _validate(tool, {"task_id": "a1"}, store)
    assert (done.valid, done.message, done.error_code) == (False, "Task a1 is not running (status: killed)", 3)


def test_the_call_stops_the_task_and_reports_it_as_json():
    store, _ = _store_with_agent()
    result = asyncio.run(build_task_stop_tool().call({"task_id": "a1"}, tool_context_for(store)))
    assert json.loads(result.content) == {
        "message": "Successfully stopped task: a1 (survey)", "task_id": "a1", "task_type": "local_agent",
        "command": "survey",
    }
    assert result.content.startswith('{"message":"Successfully')
    assert store.get().tasks["a1"].status == "killed"


def test_the_call_without_an_id_raises():
    with pytest.raises(ValueError, match="^Missing required parameter: task_id$"):
        asyncio.run(build_task_stop_tool().call({}, tool_context_for(AppStateStore())))


def test_non_ascii_descriptions_are_reported_verbatim():
    """`jsonStringify`: no `\\u` escapes (upstream's JSON.stringify)."""
    store = AppStateStore()
    register_async_agent(agent_id="a1", description="café survey", prompt="p", agent_type="Explore",
                         set_app_state=store.update, output_file="/o")
    result = asyncio.run(build_task_stop_tool().call({"task_id": "a1"}, tool_context_for(store)))
    assert '"command":"café survey"' in result.content
