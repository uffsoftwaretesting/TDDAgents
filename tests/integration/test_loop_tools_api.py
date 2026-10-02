import pytest
import os
import inspect
from pathlib import Path
from app.loop.tools.bash import build_bash_tool
from app.loop.context import ToolContext
from app.workspace.local import LocalWorkspace
from dataclasses import dataclass

@dataclass
class MockState:
    pass

@pytest.fixture
def context(tmp_path):
    ws = LocalWorkspace(str(tmp_path))
    return ToolContext(
        workspace=ws,
        cancel=lambda: None,
        get_app_state=lambda: MockState(),
        set_app_state=lambda x: None
    )

@pytest.mark.asyncio
@pytest.mark.skipif("ANTHROPIC_API_KEY" not in os.environ and "OPENAI_API_KEY" not in os.environ, reason="No LLM API Key")
async def test_bash_security_gate_llm(context, tmp_path):
    # This integration test verifies that the real LLM evaluates command injection
    tool = build_bash_tool() # mock_injection_detector is None, so it uses LLM
    
    # Injection test
    res = await tool.call({"command": "git diff $(rm -rf /)"}, context)
    assert res.is_error
    assert "command_injection_detected" in res.content
    
    # Safe test
    res_safe = await tool.call({"command": "echo 'safe'"}, context)
    assert not res_safe.is_error
    assert "safe" in res_safe.content

