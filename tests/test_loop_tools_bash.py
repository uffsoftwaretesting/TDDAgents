import pytest
from app.loop.tools.bash import build_bash_tool
from app.loop.context import ToolContext
from app.workspace.local import LocalWorkspace
from dataclasses import dataclass

@dataclass
class MockState:
    pass

@pytest.mark.asyncio
async def test_bash_tool_simple_execution(tmp_path):
    ws = LocalWorkspace(str(tmp_path))
    context = ToolContext(
        workspace=ws,
        cancel=lambda: None,
        get_app_state=lambda: MockState(),
        set_app_state=lambda x: None
    )
    tool = build_bash_tool()
    
    res = await tool.call({"command": "echo 'hello world'"}, context)
    assert not res.is_error
    assert "hello world" in res.content

@pytest.mark.asyncio
async def test_bash_tool_catches_command_injection(tmp_path):
    ws = LocalWorkspace(str(tmp_path))
    context = ToolContext(
        workspace=ws,
        cancel=lambda: None,
        get_app_state=lambda: MockState(),
        set_app_state=lambda x: None
    )
    # The bash tool should use an LLM call to evaluate the security gate.
    # We will mock the LLM check to return a rejection for injection.
    tool = build_bash_tool(mock_injection_detector=lambda cmd: "rm -rf" in cmd)
    
    res = await tool.call({"command": "git diff $(rm -rf /)"}, context)
    assert res.is_error
    assert "command_injection_detected" in res.content
