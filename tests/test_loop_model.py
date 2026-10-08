import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

from app.config.config import Config
from app.loop import model as model_mod
from app.loop.config import build_run_config
from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.context.assembly import reset_session_context
from app.loop.context.attachments import TDD_STATE_KEY
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.messages import tool_calls_in
from app.loop.model import (
    accumulate_stream,
    build_request_messages,
    convert_tool_to_langchain,
    read_todo,
    stream_call_model,
)
from app.loop.state import initial_loop_state
from app.loop.tools.base import build_tool
from app.loop.tools.types import ToolResult
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


def _state(ws=None, messages=(), ledger=None, tools=()):
    store = AppStateStore(AppState(phase_ledger=ledger)) if ledger is not None else AppStateStore()
    ctx = tool_context_for(store, workspace=ws, tools=tools)
    return initial_loop_state(messages=messages, tool_context=ctx)


def test_stream_call_model_propagates_factory_error(monkeypatch, config):
    def boom(*args, **kwargs):
        raise ValueError("Mock Error")

    monkeypatch.setattr("app.loop.model.get_chat_model", boom)
    with pytest.raises(ValueError, match="Mock Error"):
        _collect(_state(), config)


def test_stream_call_model_resolves_model_from_config_and_binds_tools(monkeypatch, config):
    model = MockModel()
    got = {}

    def factory(provider, **kwargs):
        got["provider"] = provider
        got.update(kwargs)
        return model

    monkeypatch.setattr("app.loop.model.get_chat_model", factory)
    chunks = _collect(_state(FakeWorkspace(), tools=(_tool("A"), _tool("B"))), config)
    assert [c.content for c in chunks] == ["chunk1", "chunk2"]
    assert got == {"provider": Config.CHAT_MODEL, "model": Config.MODEL, "temperature": Config.TEMPERATURE}
    assert [t["function"]["name"] for t in model.tools] == ["A", "B"]
    assert isinstance(model.sent[0], SystemMessage)


def test_stream_call_model_does_not_bind_without_tools(monkeypatch, config):
    model = MockModel()
    monkeypatch.setattr("app.loop.model.get_chat_model", lambda *a, **k: model)
    assert len(_collect(_state(), config)) == 2
    assert model.tools is None


def test_request_shape(config):
    ws = FakeWorkspace(files={"TDDAGENTS.md": "project rules", "TODO.md": "- [ ] t1"})
    history = (HumanMessage(content="hi"),)
    msgs = build_request_messages(_state(ws, history, PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)), config)

    system, user_ctx, *rest = msgs
    assert isinstance(system, SystemMessage)
    assert "# Environment" in system.content
    assert "Current Phase:" not in system.content  # run state is an attachment, not a prompt layer
    assert user_ctx.additional_kwargs == {"is_meta": True}
    assert "project rules" in user_ctx.content
    assert "Today's date is" in user_ctx.content
    assert rest[0] is history[0]
    attachment = rest[1]
    assert attachment.additional_kwargs[TDD_STATE_KEY]["phase"] == "GREEN"
    assert "Contents of TODO.md:\n- [ ] t1" in attachment.content
    assert len(rest) == 2


def test_request_omits_unchanged_attachment_and_memoizes_context(config):
    ws = FakeWorkspace(files={"TDDAGENTS.md": "v1"})
    first = build_request_messages(_state(ws), config)
    announced = first[-1]
    ws.files["TDDAGENTS.md"] = "v2"
    second = build_request_messages(_state(ws, (announced,)), config)
    assert "v1" in second[1].content  # memoized per run: the prefix stays stable
    assert second[-1] is announced  # nothing new to announce
    assert len(second) == 3


def test_system_context_is_appended(config, monkeypatch):
    monkeypatch.setattr("app.loop.context.assembly.get_system_context", lambda ws: {"gitStatus": "GS"})
    msgs = build_request_messages(_state(FakeWorkspace()), config)
    assert str(msgs[0].content).endswith("gitStatus: GS")


def test_read_todo():
    assert read_todo(None) is None
    assert read_todo(FakeWorkspace()) is None
    assert read_todo(FakeWorkspace(files={"TODO.md": "x"})) == "x"
    assert model_mod.TODO_FILE == "TODO.md"


def test_request_is_keyed_by_run_and_memoizes_sections(monkeypatch, tmp_path):
    from app.loop.context import assembly
    from app.loop.context.assembly import append_system_context, get_system_prompt
    from app.loop.prompts.sections import resolve_system_prompt_sections
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
        return [m async for m in accumulate_stream(_aiter(items))]
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


def test_chunks_are_merged_into_one_complete_ai_message():
    out = _accumulate([AIMessageChunk(content="Writing "), AIMessageChunk(content="a test.")] + _tool_call_chunks())
    assert len(out) == 1
    msg = out[0]
    assert isinstance(msg, AIMessage) and not isinstance(msg, AIMessageChunk)
    assert msg.content == "Writing a test."
    assert tool_calls_in(msg) == ({"name": "WriteFile", "args": {"file_path": "tests/test_a.py", "content": "x"},
                                   "id": "call_1", "type": "tool_call"},)


def test_parallel_tool_calls_are_kept_apart_by_index():
    chunks = [
        AIMessageChunk(content="", tool_call_chunks=[{"name": "ReadFile", "args": '{"file_path": "a"}', "id": "c1",
                                                      "index": 0}]),
        AIMessageChunk(content="", tool_call_chunks=[{"name": "ReadFile", "args": '{"file_path": "b"}', "id": "c2",
                                                      "index": 1}]),
    ]
    (msg,) = _accumulate(chunks)
    assert [(c["id"], c["args"]) for c in tool_calls_in(msg)] == [
        ("c1", {"file_path": "a"}), ("c2", {"file_path": "b"})]


def test_complete_messages_pass_through_and_flush_pending_chunks_in_order():
    whole = AIMessage(content="whole")
    out = _accumulate([AIMessageChunk(content="part-1"), whole, AIMessageChunk(content="part-2")])
    assert [m.content for m in out] == ["part-1", "whole", "part-2"]
    assert out[1] is whole
    assert all(not isinstance(m, AIMessageChunk) for m in out)


def test_non_message_items_are_dropped_and_empty_stream_yields_nothing():
    assert _accumulate([]) == []
    assert [m.content for m in _accumulate(["noise", AIMessageChunk(content="x"), 42])] == ["x"]


def test_stream_call_model_yields_one_message_for_a_chunked_reply(monkeypatch, config):
    class ChunkModel(MockModel):
        async def astream(self, messages, config=None):
            self.sent = messages
            self.config = config
            for c in [AIMessageChunk(content="a"), AIMessageChunk(content="b")] + _tool_call_chunks():
                yield c

    from app.loop.model import get_global_token_tracker

    model = ChunkModel()
    monkeypatch.setattr("app.loop.model.get_chat_model", lambda *a, **k: model)
    out = _collect(_state(tools=(_tool("WriteFile"),)), config)
    assert model.config == {"callbacks": [get_global_token_tracker()]}
    assert model.sent and isinstance(model.sent, list)
    assert len(out) == 1
    assert out[0].content == "ab"
    assert tool_calls_in(out[0])[0]["args"] == {"file_path": "tests/test_a.py", "content": "x"}
