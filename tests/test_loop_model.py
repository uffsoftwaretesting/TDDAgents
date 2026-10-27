import asyncio
import dataclasses
from typing import Any

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
)

from app.config.config import Config
from app.context import reset_session_context
from app.query.config import build_run_config
from app.query.state import initial_loop_state
from app.services.api.claude import (
    build_request_messages,
    convert_tool_to_langchain,
    frame_content_blocks,
    stream_call_model,
)
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tool import ToolResult, build_tool, tool_context_for
from app.utils.messages import tool_calls_in
from tests.conftest import FakeWorkspace


@pytest.fixture(autouse=True)
def fresh_sessions(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # no host memory files leak in
    reset_session_context()
    yield
    reset_session_context()


def _tool(name="TestTool"):
    return build_tool(
        name=name,
        prompt="A test tool",
        input_schema={"type": "object", "properties": {"foo": {"type": "string"}}},
        call=lambda args, ctx: ToolResult(content=""),
    )


def test_convert_tool_to_langchain():
    assert convert_tool_to_langchain(_tool()) == {
        "type": "function",
        "function": {
            "name": "TestTool",
            "description": "A test tool",
            "parameters": {"type": "object", "properties": {"foo": {"type": "string"}}},
        },
    }


class MockModel:
    def __init__(self):
        self.tools: Any = None
        self.sent: Any = None

    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def astream(self, messages):
        self.sent = messages
        yield AIMessage(content="chunk1")
        yield AIMessage(content="chunk2")


def _collect(state, config):
    async def go():
        return [c async for c in stream_call_model(state, config)]
    return asyncio.run(go())


@pytest.fixture
def config():
    return build_run_config(run_id="test", postgres_checkpointing=False)


def _state(ws=None, messages=(), ledger=None, tools=(), todos=()):
    app_state = AppState()
    if ledger is not None:
        app_state = dataclasses.replace(app_state, phase_ledger=ledger)
    if todos:
        app_state = dataclasses.replace(app_state, todos={"main": tuple(todos)})
    store = AppStateStore(app_state)
    ctx = tool_context_for(store, workspace=ws, tools=tools)
    return initial_loop_state(messages=messages, tool_context=ctx)


def test_stream_call_model_gives_up_on_an_unretryable_factory_error(monkeypatch, config):
    """A ValueError carries no retryable status, so `shouldRetry` refuses it on attempt 1."""
    from app.services.api.with_retry import CannotRetryError

    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise ValueError("Mock Error")

    monkeypatch.setattr("app.services.api.claude.get_chat_model", boom)
    with pytest.raises(CannotRetryError) as excinfo:
        _collect(_state(), config)
    assert isinstance(excinfo.value.original_error, ValueError)
    assert len(calls) == 1


def test_stream_call_model_resolves_model_from_config_and_binds_tools(monkeypatch, config):
    model = MockModel()
    got = {}

    def factory(provider, **kwargs):
        got["provider"] = provider
        got.update(kwargs)
        return model

    monkeypatch.setattr("app.services.api.claude.get_chat_model", factory)
    chunks = _collect(_state(FakeWorkspace(), tools=(_tool("A"), _tool("B"))), config)
    assert [c.content for c in chunks] == ["chunk1", "chunk2"]
    assert got == {"provider": Config.CHAT_MODEL, "model": Config.MODEL, "temperature": Config.TEMPERATURE}
    assert [t["function"]["name"] for t in model.tools] == ["A", "B"]
    assert isinstance(model.sent[0], SystemMessage)


def test_stream_call_model_does_not_bind_without_tools(monkeypatch, config):
    model = MockModel()
    monkeypatch.setattr("app.services.api.claude.get_chat_model", lambda *a, **k: model)
    assert len(_collect(_state(), config)) == 2
    assert model.tools is None


def test_request_shape(config):
    from app.tools.todo_write_tool.todo_write_tool import TodoItem

    ws = FakeWorkspace(files={"TDDAGENTS.md": "project rules"})
    history = (HumanMessage(content="hi"),)
    todos = (TodoItem(content="t1", status="pending", active_form="doing t1"),)
    msgs = build_request_messages(
        _state(ws, history, PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True), todos=todos), config)

    system, user_ctx, *rest = msgs
    assert isinstance(system, SystemMessage)
    assert "# Environment" in system.content
    assert "Current Phase:" not in system.content  # run state is an attachment, not a prompt layer
    assert user_ctx.additional_kwargs == {"is_meta": True}
    assert "project rules" in user_ctx.content
    assert "Today's date is" in user_ctx.content
    # Phase 5: the request is the stored history; attachments are persisted by the engine.
    assert rest == [history[0]]


def test_request_memoizes_the_session_context(config):
    ws = FakeWorkspace(files={"TDDAGENTS.md": "v1"})
    first = build_request_messages(_state(ws), config)
    ws.files["TDDAGENTS.md"] = "v2"
    second = build_request_messages(_state(ws), config)
    assert "v1" in first[1].content
    assert "v1" in second[1].content  # memoized per run: the prefix stays stable
    assert len(second) == 2  # system + user context; this state has no history


def test_system_context_is_appended(config, monkeypatch):
    monkeypatch.setattr("app.context.get_system_context", lambda ws: {"gitStatus": "GS"})
    msgs = build_request_messages(_state(FakeWorkspace()), config)
    assert str(msgs[0].content).endswith("gitStatus: GS")


def test_request_is_keyed_by_run_and_memoizes_sections(monkeypatch, tmp_path):
    import app.context as assembly
    from app.constants.system_prompt_sections import resolve_system_prompt_sections
    from app.context import append_system_context, get_system_prompt
    from app.workspace.local import LocalWorkspace

    calls = []
    real = assembly.compute_simple_env_info

    def counting(ws, model_id):
        calls.append((ws, model_id))
        return real(ws, model_id)

    monkeypatch.setattr(assembly, "compute_simple_env_info", counting)
    ws = LocalWorkspace(str(tmp_path))
    run_a = build_run_config(run_id="A", postgres_checkpointing=False)
    run_b = build_run_config(run_id="B", postgres_checkpointing=False)
    first = build_request_messages(_state(ws), run_a)
    build_request_messages(_state(ws), run_a)
    assert calls == [(ws, Config.MODEL)]  # one computation per run, with this workspace and model
    build_request_messages(_state(ws), run_b)
    assert len(calls) == 2

    expected = "\n\n".join(append_system_context(
        resolve_system_prompt_sections(get_system_prompt(ws, Config.MODEL)), {}))
    assert first[0].content == expected
    assert f"You are powered by the model {Config.MODEL}." in first[0].content
    assert f"Primary working directory: {ws.root}" in first[0].content


# ── whole-message accumulation (Phase 0: the engine never sees a partial tool call) ──


async def _aiter(items):
    for item in items:
        yield item


def _accumulate(items):
    async def go():
        return [m async for m in frame_content_blocks(_aiter(items))]
    return asyncio.run(go())


def _tool_call_chunks():
    return [
        AIMessageChunk(content="", tool_call_chunks=[{"name": "WriteFile", "args": "", "id": "call_1", "index": 0}]),
        AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": '{"file_path": "tests/t', "id": None,
                                                      "index": 0}]),
        AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": 'est_a.py", "content": "x"}', "id": None,
                                                      "index": 0}]),
    ]


def test_partial_chunks_never_expose_tool_calls():
    """The defect this fixes: a mid-stream chunk parses to a call with no name or args."""
    partial = _tool_call_chunks()[1]
    assert tool_calls_in(partial)[0]["name"] == "" or tool_calls_in(partial)[0]["args"] == {}


def test_each_content_block_becomes_its_own_complete_message():
    """
    Upstream emits one assistant message per content block, at `content_block_stop`.
    Text then a tool call is two messages, and the tool call is whole in the second.
    """
    out = _accumulate([AIMessageChunk(content="Writing "), AIMessageChunk(content="a test.")] + _tool_call_chunks())
    assert len(out) == 2
    assert all(isinstance(m, AIMessage) and not isinstance(m, AIMessageChunk) for m in out)
    assert out[0].content == "Writing a test."
    assert tool_calls_in(out[0]) == ()
    assert out[1].content == ""
    assert tool_calls_in(out[1]) == ({"name": "WriteFile", "args": {"file_path": "tests/test_a.py", "content": "x"},
                                      "id": "call_1", "type": "tool_call"},)


def test_a_tool_block_closes_when_the_next_block_opens_not_at_stream_end():
    """What makes eager dispatch real: call 1 is complete while call 2 is still arriving."""
    chunks = [
        AIMessageChunk(content="", tool_call_chunks=[{"name": "ReadFile", "args": '{"file_path": "a"}', "id": "c1",
                                                      "index": 0}]),
        AIMessageChunk(content="", tool_call_chunks=[{"name": "ReadFile", "args": '{"file_pa', "id": "c2",
                                                      "index": 1}]),
        AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": 'th": "b"}', "id": None, "index": 1}]),
    ]
    out = _accumulate(chunks)
    assert len(out) == 2
    assert [tool_calls_in(m)[0]["id"] for m in out] == ["c1", "c2"]
    assert [tool_calls_in(m)[0]["args"] for m in out] == [{"file_path": "a"}, {"file_path": "b"}]


def test_two_calls_in_one_chunk_become_two_messages():
    chunk = AIMessageChunk(content="", tool_call_chunks=[
        {"name": "ReadFile", "args": '{"file_path": "a"}', "id": "c1", "index": 0},
        {"name": "ReadFile", "args": '{"file_path": "b"}', "id": "c2", "index": 1},
    ])
    out = _accumulate([chunk])
    assert [tool_calls_in(m)[0]["id"] for m in out] == ["c1", "c2"]


def test_text_and_a_tool_call_in_the_same_chunk_split_in_order():
    chunk = AIMessageChunk(content="thinking", tool_call_chunks=[
        {"name": "ReadFile", "args": '{"file_path": "a"}', "id": "c1", "index": 0}])
    out = _accumulate([chunk])
    assert [m.content for m in out] == ["thinking", ""]
    assert tool_calls_in(out[1])[0]["id"] == "c1"


def test_a_metadata_only_final_chunk_rides_along_and_opens_no_block():
    """OpenAI's last frame carries finish_reason and no content; it must not make a message."""
    out = _accumulate([
        AIMessageChunk(content="answer"),
        AIMessageChunk(content="", response_metadata={"finish_reason": "stop"}),
    ])
    assert [m.content for m in out] == ["answer"]
    assert out[0].response_metadata.get("finish_reason") == "stop"


def test_a_stream_of_only_metadata_yields_nothing():
    assert _accumulate([AIMessageChunk(content="", response_metadata={"finish_reason": "stop"})]) == []


def test_complete_messages_pass_through_and_flush_pending_chunks_in_order():
    whole = AIMessage(content="whole")
    out = _accumulate([AIMessageChunk(content="part-1"), whole, AIMessageChunk(content="part-2")])
    assert [m.content for m in out] == ["part-1", "whole", "part-2"]
    assert out[1] is whole
    assert all(not isinstance(m, AIMessageChunk) for m in out)


def test_non_message_items_are_dropped_and_empty_stream_yields_nothing():
    assert _accumulate([]) == []
    assert [m.content for m in _accumulate(["noise", AIMessageChunk(content="x"), 42])] == ["x"]


def test_stream_call_model_frames_a_chunked_reply_into_blocks(monkeypatch, config):
    class ChunkModel(MockModel):
        async def astream(self, messages, config=None):
            self.sent = messages
            self.config = config
            for c in [AIMessageChunk(content="a"), AIMessageChunk(content="b")] + _tool_call_chunks():
                yield c

    from app.services.api.claude import get_global_token_tracker

    model = ChunkModel()
    monkeypatch.setattr("app.services.api.claude.get_chat_model", lambda *a, **k: model)
    out = _collect(_state(tools=(_tool("WriteFile"),)), config)
    assert model.config == {"callbacks": [get_global_token_tracker()]}
    assert model.sent and isinstance(model.sent, list)
    assert [m.content for m in out] == ["ab", ""]
    assert tool_calls_in(out[1])[0]["args"] == {"file_path": "tests/test_a.py", "content": "x"}
