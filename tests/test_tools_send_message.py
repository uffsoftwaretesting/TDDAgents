"""Phase 8: `SendMessage` (`tools/SendMessageTool/SendMessageTool.ts`, local-agent branches only)
and `resumeAgentBackground` (`tools/AgentTool/resumeAgent.ts`)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.state.app_state_store import AppStateStore
from app.tasks.local_agent_task.local_agent_task import register_async_agent
from app.tool import tool_context_for
from app.tools.agent_tool.resume_agent import resume_agent_background
from app.tools.send_message_tool.send_message_tool import (
    AGENT_TEAMS_ENV,
    SEND_MESSAGE_TOOL_NAME,
    build_send_message_tool,
)
from app.utils.message_queue_manager import CommandQueue
from app.utils.session_storage import TranscriptStore
from tests.test_agents_agent_tool import DEFS, EXPLORE, TESTER, _tool
from tests.test_loop_engine import CONFIG, FakeModel, make_fake_deps

AGENT_ID = "a0123456789abcdef"


def _ctx(tmp_path):
    store = AppStateStore()
    transcript = TranscriptStore("loop-1", tmp_path, version="v", git_branch=None)
    ctx = tool_context_for(store, run_id="loop-1", command_queue=CommandQueue(), transcript_store=transcript,
                           messages=(HumanMessage(content="coordinate"),))
    return store, ctx, transcript


def _send_tool(model: FakeModel | None = None):
    deps = make_fake_deps(model or FakeModel([[AIMessage(content="resumed answer")]]))
    return build_send_message_tool(definitions=DEFS, worker_tools=(), config=CONFIG, deps=deps)


def _drive(coro_factory, store: AppStateStore):
    """Run in one event loop, then wait for every background task to finish."""

    async def go() -> Any:
        result = await coro_factory()
        for _ in range(200):
            await asyncio.sleep(0.01)
            if all(t.status != "running" for t in store.get().tasks.values()):
                break
        return result

    return asyncio.run(go())


def _sync_agent(ctx, store, agent_type: str = "Explore") -> str:
    """A finished synchronous agent: a sidechain transcript and metadata, but no task."""
    tool = _tool(FakeModel([[AIMessage(content="first answer")]]))
    args = {"description": "survey the code", "prompt": "find add()", "subagent_type": agent_type}
    result = asyncio.run(tool.call(args, ctx))
    agent_id: str = result.metadata["agent_id"]
    return agent_id


def _send(tool, args: dict[str, Any], ctx, store):
    result = _drive(lambda: tool.call(args, ctx), store)
    return json.loads(result.content)


class TestShape:
    def test_name_schema_and_flags_are_upstreams(self):
        tool = _send_tool()
        assert (tool.name, SEND_MESSAGE_TOOL_NAME) == ("SendMessage", "SendMessage")
        assert tool.input_schema == {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient: an agent ID returned by the Agent tool"},
                "summary": {
                    "type": "string",
                    "description": "A 5-10 word summary shown as a preview in the UI (required when message is a "
                                   "string)",
                },
                "message": {"type": "string", "description": "Plain text message content"},
            },
            "required": ["to", "message"],
            "additionalProperties": False,
        }
        assert tool.is_read_only({"to": "x", "message": "m"}) is True
        assert tool.is_concurrency_safe({}) is False
        assert tool.max_result_size_chars == 100_000
        assert tool.prompt.startswith("# SendMessage\n\nSend a message to another agent.")
        assert "Broadcast" not in tool.prompt and "shutdown_response" not in tool.prompt

    @pytest.mark.parametrize("value,enabled", [(None, False), ("0", False), ("1", True), ("true", True)])
    def test_it_is_enabled_only_when_agent_teams_are_opted_into(self, monkeypatch, value, enabled):
        if value is None:
            monkeypatch.delenv(AGENT_TEAMS_ENV, raising=False)
        else:
            monkeypatch.setenv(AGENT_TEAMS_ENV, value)
        assert AGENT_TEAMS_ENV == "TDDAGENTS_EXPERIMENTAL_AGENT_TEAMS"
        assert _send_tool().is_enabled() is enabled


class TestValidation:
    @pytest.mark.parametrize("args,message", [
        ({"to": "  ", "message": "m", "summary": "s"}, "to must not be empty"),
        ({"to": "x@team", "message": "m", "summary": "s"},
         'to must be a bare teammate name or "*" — there is only one team per session'),
        ({"to": AGENT_ID, "message": "m"}, "summary is required when message is a string"),
        ({"to": AGENT_ID, "message": "m", "summary": " "}, "summary is required when message is a string"),
        ({"to": "researcher", "message": "m", "summary": "s"},
         "to must be an agent ID returned by the Agent tool: teammates are not available in this build"),
        ({"to": "*", "message": "m", "summary": "s"},
         "to must be an agent ID returned by the Agent tool: teammates are not available in this build"),
    ])
    def test_upstreams_checks_with_error_code_9(self, tmp_path, args, message):
        _, ctx, _ = _ctx(tmp_path)
        result = _send_tool().validate_input(args, ctx)
        assert (result.valid, result.message, result.error_code) == (False, message, 9)

    def test_a_well_formed_message_is_valid(self, tmp_path):
        _, ctx, _ = _ctx(tmp_path)
        assert _send_tool().validate_input({"to": AGENT_ID, "message": "m", "summary": "s"}, ctx).valid is True


def test_a_running_agent_gets_the_message_queued(tmp_path):
    store, ctx, _ = _ctx(tmp_path)
    register_async_agent(agent_id=AGENT_ID, description="d", prompt="p", agent_type="Explore",
                         set_app_state=store.update, output_file="/o")
    out = _send(_send_tool(), {"to": AGENT_ID, "message": "also check sub()", "summary": "s"}, ctx, store)
    assert out == {"success": True,
                   "message": f"Message queued for delivery to {AGENT_ID} at its next tool round."}
    assert store.get().tasks[AGENT_ID].pending_messages == ("also check sub()",)


def test_a_finished_agent_without_a_task_is_resumed_from_its_transcript(tmp_path):
    store, ctx, transcript = _ctx(tmp_path)
    agent_id = _sync_agent(ctx, store)
    model = FakeModel([[AIMessage(content="resumed answer")]])
    out = _send(_send_tool(model), {"to": agent_id, "message": "now check sub()", "summary": "s"}, ctx, store)
    output_file = transcript.path.parent / "loop-1" / "tasks" / f"{agent_id}.output"
    assert out == {
        "success": True,
        "message": f'Agent "{agent_id}" had no active task; resumed from transcript in the background with '
                   f"your message. You'll be notified when it finishes. Output: {output_file}",
    }
    contents = [m.content for m in model.seen[0].messages]
    assert contents[:3] == ["find add()", "first answer", "now check sub()"]
    task = store.get().tasks[agent_id]
    assert (task.status, task.description, task.agent_type, task.result) == (
        "completed", "survey the code", "Explore", "resumed answer")
    (note,) = ctx.command_queue.snapshot()
    assert f"<task-id>{agent_id}</task-id>" in note.value and "<result>resumed answer</result>" in note.value


def test_a_stopped_task_is_resumed_with_its_status_named(tmp_path):
    store, ctx, transcript = _ctx(tmp_path)
    agent_id = _sync_agent(ctx, store)
    register_async_agent(agent_id=agent_id, description="d", prompt="p", agent_type="Explore",
                         set_app_state=store.update, output_file="/o")
    from app.tasks.local_agent_task.local_agent_task import complete_agent_task

    complete_agent_task(agent_id, "r", store.update)
    out = _send(_send_tool(), {"to": agent_id, "message": "again", "summary": "s"}, ctx, store)
    output_file = transcript.path.parent / "loop-1" / "tasks" / f"{agent_id}.output"
    assert out == {
        "success": True,
        "message": f'Agent "{agent_id}" was stopped (completed); resumed it in the background with your message. '
                   f"You'll be notified when it finishes. Output: {output_file}",
    }
    assert store.get().tasks[agent_id].status == "completed"
    assert store.get().tasks[agent_id].result == "resumed answer"


def test_an_agent_with_no_transcript_cannot_be_resumed(tmp_path):
    store, ctx, _ = _ctx(tmp_path)
    out = _send(_send_tool(), {"to": AGENT_ID, "message": "m", "summary": "s"}, ctx, store)
    assert out == {
        "success": False,
        "message": f'Agent "{AGENT_ID}" is registered but has no transcript to resume. It may have been cleaned '
                   f"up. (No transcript found for agent ID: {AGENT_ID})",
    }
    assert store.get().tasks == {}


def test_a_stopped_task_that_cannot_resume_reports_why(tmp_path):
    store, ctx, _ = _ctx(tmp_path)
    register_async_agent(agent_id=AGENT_ID, description="d", prompt="p", agent_type="Explore",
                         set_app_state=store.update, output_file="/o")
    from app.tasks.local_agent_task.local_agent_task import fail_agent_task

    fail_agent_task(AGENT_ID, "boom", store.update)
    out = _send(_send_tool(), {"to": AGENT_ID, "message": "m", "summary": "s"}, ctx, store)
    assert out == {
        "success": False,
        "message": f'Agent "{AGENT_ID}" is stopped (failed) and could not be resumed: '
                   f"No transcript found for agent ID: {AGENT_ID}",
    }


def test_a_phase_agent_is_never_resumed_in_the_background(tmp_path):
    """TDD: a resumed agent always runs async, and a phase agent may not (its RunTests would be lost)."""
    store, ctx, _ = _ctx(tmp_path)
    agent_id = _sync_agent(ctx, store, agent_type="tester")
    model = FakeModel([])
    out = _send(_send_tool(model), {"to": agent_id, "message": "fix the test", "summary": "s"}, ctx, store)
    assert out["success"] is False
    assert out["message"].startswith(f'Agent "{agent_id}" is registered but has no transcript to resume.')
    assert f"Agent '{TESTER.name}' declares TDD phase RED and cannot run in the background" in out["message"]
    assert model.seen == [] and store.get().tasks == {}


class TestResume:
    def test_an_unknown_agent_type_cannot_be_resumed(self, tmp_path):
        store, ctx, transcript = _ctx(tmp_path)
        agent_id = _sync_agent(ctx, store)
        transcript.write_agent_metadata(agent_id, {"agentType": "gone"})
        with pytest.raises(ValueError, match="^Agent type 'gone' not found. Available agents: "):
            asyncio.run(resume_agent_background(agent_id=agent_id, prompt="p", context=ctx, definitions=DEFS,
                                                worker_tools=(), config=CONFIG,
                                                deps=make_fake_deps(FakeModel([]))))

    def test_missing_metadata_cannot_route_the_resume(self, tmp_path):
        store, ctx, transcript = _ctx(tmp_path)
        agent_id = _sync_agent(ctx, store)
        transcript.agent_metadata_path(agent_id).unlink()
        with pytest.raises(ValueError, match=f"^No agent type recorded for agent ID: {agent_id}$"):
            asyncio.run(resume_agent_background(agent_id=agent_id, prompt="p", context=ctx, definitions=DEFS,
                                                worker_tools=(), config=CONFIG,
                                                deps=make_fake_deps(FakeModel([]))))

    def test_a_resume_without_a_description_is_labelled_resumed(self, tmp_path):
        store, ctx, transcript = _ctx(tmp_path)
        agent_id = _sync_agent(ctx, store)
        transcript.write_agent_metadata(agent_id, {"agentType": EXPLORE.name})
        result = _drive(lambda: resume_agent_background(
            agent_id=agent_id, prompt="p", context=ctx, definitions=DEFS, worker_tools=(), config=CONFIG,
            deps=make_fake_deps(FakeModel([[AIMessage(content="x")]]))), store)
        assert (result.agent_id, result.description) == (agent_id, "(resumed)")
        assert store.get().tasks[agent_id].description == "(resumed)"


# ── mutation-gate pins (Phase 8) ────────────────────────────────────────────

def test_the_result_is_compact_json(tmp_path):
    store, ctx, _ = _ctx(tmp_path)
    register_async_agent(agent_id=AGENT_ID, description="d", prompt="p", agent_type="Explore",
                         set_app_state=store.update, output_file="/o")
    result = _drive(lambda: _send_tool().call({"to": AGENT_ID, "message": "m", "summary": "s"}, ctx), store)
    assert result.content == (
        f'{{"success":true,"message":"Message queued for delivery to {AGENT_ID} at its next tool round."}}')


def test_non_ascii_failures_are_reported_verbatim(tmp_path):
    store, ctx, transcript = _ctx(tmp_path)
    agent_id = _sync_agent(ctx, store)
    transcript.write_agent_metadata(agent_id, {"agentType": "géo"})
    result = _drive(lambda: _send_tool().call({"to": agent_id, "message": "m", "summary": "s"}, ctx), store)
    assert "Agent type 'géo' not found" in result.content


def test_a_resumed_agent_is_started_on_the_tools_clock(tmp_path):
    store, ctx, _ = _ctx(tmp_path)
    agent_id = _sync_agent(ctx, store)
    deps = make_fake_deps(FakeModel([[AIMessage(content="again")]]))
    tool = build_send_message_tool(definitions=DEFS, worker_tools=(), config=CONFIG, deps=deps, now=lambda: 42.0)
    _drive(lambda: tool.call({"to": agent_id, "message": "m", "summary": "s"}, ctx), store)
    task = store.get().tasks[agent_id]
    assert (task.start_time, task.end_time) == (42.0, 42.0)


class _Workspace:
    """A workspace with a root and no shell: the env block names `/ws`, unlike no workspace."""

    root = "/ws"

    def execute(self, cmd: str) -> Any:
        raise OSError("no shell")


def _resume_ctx(tmp_path):
    from app.tools.agent_tool.agent_memory import AgentMemoryStore

    store = AppStateStore()
    transcript = TranscriptStore("loop-1", tmp_path, version="v", git_branch=None)
    ctx = tool_context_for(store, run_id="loop-1", command_queue=CommandQueue(), transcript_store=transcript,
                           messages=(HumanMessage(content="coordinate"),), workspace=_Workspace(),
                           agent_memory_store=AgentMemoryStore(base_dir=tmp_path / "mem"))
    return store, ctx, transcript


@pytest.mark.parametrize("agent_model", ["haiku", "inherit"])
def test_resume_hands_the_worker_everything_it_recorded(tmp_path, agent_model):
    import dataclasses

    from app.context import enhance_system_prompt_with_env_details
    from app.tools.agent_tool.agent_tool import agent_system_prompt
    from app.utils.model.model import get_agent_model

    explore = dataclasses.replace(EXPLORE, model=agent_model, memory="run")
    defs = {**DEFS, "Explore": explore}
    store, ctx, transcript = _resume_ctx(tmp_path)
    tool = _tool(FakeModel([[AIMessage(content="first answer")]]), definitions=defs)
    first = asyncio.run(tool.call(
        {"description": "survey the code", "prompt": "find add()", "subagent_type": "Explore"}, ctx))
    agent_id = first.metadata["agent_id"]

    model = FakeModel([[AIMessage(content="again")]])
    clock = iter([7.0, 8.0, 9.0, 10.0])
    resume_ctx = dataclasses.replace(ctx, tool_use_id="toolu_2")
    result = _drive(lambda: resume_agent_background(
        agent_id=agent_id, prompt="now", context=resume_ctx, definitions=defs, worker_tools=(), config=CONFIG,
        deps=make_fake_deps(model), now=lambda: next(clock)), store)

    worker = model.seen[0].tool_context
    expected_model = get_agent_model(agent_model, CONFIG.model, None)
    assert worker.agent_system_prompt == enhance_system_prompt_with_env_details(
        [agent_system_prompt(explore, ctx)], expected_model, (), ctx.workspace)
    assert "Working directory: /ws" in worker.agent_system_prompt[-1]
    task = store.get().tasks[agent_id]
    output_file = transcript.path.parent / "loop-1" / "tasks" / f"{agent_id}.output"
    assert result.output_file == str(output_file)
    assert (task.prompt, task.output_file, task.start_time, task.end_time) == ("now", str(output_file), 7.0, 9.0)
    assert output_file.resolve() == transcript.agent_transcript_path(agent_id).resolve()
    assert worker.agent_id == agent_id and worker.cancel is task.cancel
    assert worker.get_app_state().tool_permission_context.should_avoid_permission_prompts is True
    assert transcript.read_agent_metadata(agent_id)["description"] == "survey the code"
    (note,) = ctx.command_queue.snapshot()
    assert f"<output-file>{output_file}</output-file>" in note.value
    assert '<summary>Agent "survey the code" completed</summary>' in note.value
    assert task.tool_use_id == "toolu_2" and "<tool-use-id>toolu_2</tool-use-id>" in note.value


def test_without_a_transcript_store_the_agent_is_named(tmp_path):
    ctx = tool_context_for(AppStateStore())
    with pytest.raises(ValueError, match=f"^No transcript found for agent ID: {AGENT_ID}$"):
        asyncio.run(resume_agent_background(agent_id=AGENT_ID, prompt="p", context=ctx, definitions=DEFS,
                                            worker_tools=(), config=CONFIG, deps=make_fake_deps(FakeModel([]))))


def test_an_unknown_agent_type_lists_every_available_agent(tmp_path):
    store, ctx, transcript = _ctx(tmp_path)
    agent_id = _sync_agent(ctx, store)
    transcript.write_agent_metadata(agent_id, {"agentType": "gone"})
    with pytest.raises(ValueError) as err:
        asyncio.run(resume_agent_background(agent_id=agent_id, prompt="p", context=ctx, definitions=DEFS,
                                            worker_tools=(), config=CONFIG, deps=make_fake_deps(FakeModel([]))))
    assert str(err.value) == f"Agent type 'gone' not found. Available agents: {', '.join(DEFS)}"
