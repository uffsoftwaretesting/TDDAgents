"""Phase 7: coordinator mode (`coordinator/coordinatorMode.ts`, `utils/toolPool.ts` ->
`applyCoordinatorToolFilter`, `constants/tools.ts` -> `COORDINATOR_MODE_ALLOWED_TOOLS`)."""

from __future__ import annotations

import re

from app.loop.config import build_run_config
from app.loop.coordinator import (
    COORDINATOR_MODE_ALLOWED_TOOLS,
    COORDINATOR_MODE_ENV,
    INTERNAL_WORKER_TOOLS,
    apply_coordinator_tool_filter,
    get_coordinator_system_prompt,
    get_coordinator_user_context,
    is_coordinator_mode,
)
from app.loop.tools.base import build_tool
from app.loop.tools.types import ToolResult


def _tool(name: str):
    return build_tool(name=name, prompt=name, call=lambda a, c: ToolResult(content=""))


def test_the_mode_is_an_env_gate_frozen_at_entry(monkeypatch):
    monkeypatch.delenv(COORDINATOR_MODE_ENV, raising=False)
    off = build_run_config("r", postgres_checkpointing=False)
    assert off.gates.coordinator_mode is False and is_coordinator_mode(off) is False
    monkeypatch.setenv(COORDINATOR_MODE_ENV, "1")
    on = build_run_config("r", postgres_checkpointing=False)
    assert on.gates.coordinator_mode is True and is_coordinator_mode(on) is True
    monkeypatch.setenv(COORDINATOR_MODE_ENV, "0")
    assert build_run_config("r", postgres_checkpointing=False).gates.coordinator_mode is False


def test_the_coordinator_keeps_only_orchestration_tools():
    assert COORDINATOR_MODE_ALLOWED_TOOLS == {"Agent", "TaskStop", "SendMessage", "StructuredOutput"}
    tools = [_tool(n) for n in ("ReadFile", "Agent", "Bash", "RunTests", "TaskStop")]
    assert [t.name for t in apply_coordinator_tool_filter(tools)] == ["Agent", "TaskStop"]


def test_the_user_context_names_the_worker_tools():
    from app.loop.agents.resolution import ASYNC_AGENT_ALLOWED_TOOLS

    context = get_coordinator_user_context()
    expected = sorted(ASYNC_AGENT_ALLOWED_TOOLS - INTERNAL_WORKER_TOOLS)
    assert context == {
        "workerToolsContext": f"Workers spawned via the Agent tool have access to these tools: {', '.join(expected)}"
    }


def test_the_system_prompt_is_rendered_with_no_placeholder_left():
    prompt = get_coordinator_system_prompt()
    assert not re.search(r"\{\{[A-Z_]+\}\}", prompt)
    for name in ("tester", "developer", "refactorer", "verification", "Agent", "RunTests"):
        assert name in prompt
    assert prompt.startswith("You are a TDD coordinator")
