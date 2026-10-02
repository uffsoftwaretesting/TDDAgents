import pytest
from app.loop.model import stream_call_model, convert_tool_to_langchain
from app.loop.state import initial_loop_state
from app.loop.context import tool_context_for, AppStateStore
from app.loop.config import build_run_config
from app.loop.tools.base import build_tool
from langchain_core.messages import AIMessage

def test_convert_tool_to_langchain():
    tool = build_tool(
        name="TestTool",
        prompt="A test tool",
        input_schema={"type": "object", "properties": {"foo": {"type": "string"}}},
        call=lambda args, ctx: None
    )
    lc_tool = convert_tool_to_langchain(tool)
    assert lc_tool["type"] == "function"
    assert lc_tool["function"]["name"] == "TestTool"
    assert lc_tool["function"]["description"] == "A test tool"
    assert lc_tool["function"]["parameters"]["properties"]["foo"]["type"] == "string"

@pytest.mark.asyncio
async def test_stream_call_model_raises_exception(monkeypatch):
    store = AppStateStore()
    ctx = tool_context_for(store)
    state = initial_loop_state(messages=(), tool_context=ctx)
    config = build_run_config(run_id="test", postgres_checkpointing=False)
    
    def mock_get_chat_model(*args, **kwargs):
        raise ValueError("Mock Error")
        
    monkeypatch.setattr("app.loop.model.get_chat_model", mock_get_chat_model)
    
    with pytest.raises(ValueError, match="Mock Error"):
        async for _ in stream_call_model(state, config):
            pass

@pytest.mark.asyncio
async def test_stream_call_model_success(monkeypatch):
    store = AppStateStore()
    ctx = tool_context_for(store)
    state = initial_loop_state(messages=(), tool_context=ctx)
    config = build_run_config(run_id="test", postgres_checkpointing=False)

    class MockModel:
        def bind_tools(self, tools):
            self.tools = tools
            return self
        
        async def astream(self, messages):
            yield AIMessage(content="chunk1")
            yield AIMessage(content="chunk2")

    def mock_get_chat_model(*args, **kwargs):
        return MockModel()
        
    monkeypatch.setattr("app.loop.model.get_chat_model", mock_get_chat_model)
    
    chunks = []
    async for chunk in stream_call_model(state, config):
        chunks.append(chunk)
        
    assert len(chunks) == 2
    assert chunks[0].content == "chunk1"
    assert chunks[1].content == "chunk2"
