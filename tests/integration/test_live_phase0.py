"""
Live Phase 0 acceptance: the production runner with the real model on a real workspace.

Runs only with RUN_LIVE_LLM_TESTS=1 and a real OPENAI_API_KEY in the repo's .env (see the
`live` marker in tests/conftest.py). It exercises the whole Phase 0 path with nothing faked:
`stream_call_model` (whole-message accumulation over a real provider stream), the file tools,
the TDD permission gate, and RunTests running pytest in the session venv.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from tests.conftest import read_dotenv_key

pytestmark = pytest.mark.live

SPEC = (
    "Using strict TDD in this empty Python project: first write a pytest test in "
    "tests/test_calculator.py for a function add(a, b) in calculator.py that returns a + b, "
    "run it with RunTests and confirm it fails, then write calculator.py so the test passes, "
    "and run RunTests again to confirm it passes. Then stop."
)


@pytest.fixture
def real_openai_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = read_dotenv_key("OPENAI_API_KEY")
    if key is None:
        pytest.skip("no real OPENAI_API_KEY in .env")
    monkeypatch.setenv("OPENAI_API_KEY", key)
    from app.config.config import Config

    monkeypatch.setattr(Config, "OPENAI_API_KEY", key)
    return key


def test_live_runner_completes_a_red_green_cycle(real_openai_key: str, python_workspace) -> None:
    from app.loop.runner import tdd_loop_runner

    result = asyncio.run(tdd_loop_runner(SPEC, 0, {"session_id": "live"}, workspace=python_workspace))

    assert result.terminal_reason == "completed", result.error_message
    assert result.red_confirmed is True
    assert result.green_passed is True
    assert python_workspace.exists("tests/test_calculator.py")
    assert python_workspace.exists("calculator.py")
    check = python_workspace.execute("python -m pytest -q tests")
    assert check.exit_code == 0, check.stdout


def test_live_model_call_yields_whole_messages(real_openai_key: str, python_workspace) -> None:
    """A real stream with a tool call must reach the loop as one complete AIMessage."""
    from langchain_core.messages import AIMessageChunk, HumanMessage

    from app.loop.config import build_run_config
    from app.loop.context import AppStateStore, tool_context_for
    from app.loop.messages import tool_calls_in
    from app.loop.model import stream_call_model
    from app.loop.runner import build_builtin_tools
    from app.loop.state import initial_loop_state

    tools = tuple(build_builtin_tools())
    ctx = tool_context_for(AppStateStore(), tools=tools, workspace=python_workspace)
    prompt = HumanMessage(content="Call the Glob tool with pattern '**/*.py'. Do nothing else.")
    state = initial_loop_state((prompt,), ctx)

    async def collect() -> list:
        return [m async for m in stream_call_model(state, build_run_config("live-msg", postgres_checkpointing=False))]

    messages = asyncio.run(collect())
    assert messages and all(not isinstance(m, AIMessageChunk) for m in messages)
    calls = [c for m in messages for c in tool_calls_in(m)]
    assert calls and calls[0]["name"] == "Glob"
    assert calls[0]["args"].get("pattern")
    assert calls[0]["id"]
    assert os.environ["OPENAI_API_KEY"] == real_openai_key
