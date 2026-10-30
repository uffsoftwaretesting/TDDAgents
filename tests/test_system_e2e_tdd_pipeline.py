"""
End-to-End System Integration Test Suite for TDDAgents.

Validates the integrated multi-turn lifecycle across Parts D, E, F, G, and H:
1. End-to-end autonomous TDD cycle (Red -> Green -> Refactor) using run_loop,
   LocalWorkspace, real file edits, test execution, and phase transitions.
2. Premature exit enforcement: the stop hooks send every early stop back (Phase 12: no latch).
3. Cooperative cancellation and abort tree cascading with history-repair placeholder injection.
4. Optional live LLM roundtrip if OPENAI_API_KEY is configured in the environment or .env.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.query.config import build_run_config
from app.query.deps import LoopDeps
from app.query.query import drain, run_loop
from app.query.state import CompactionResult, initial_loop_state
from app.query.stop_hooks import build_stop_hooks_runner
from app.query.transitions import Continue, Terminal
from app.services.tools.tool_orchestration import run_tools
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.run_tests_tool.run_tests_tool import (
    SuiteExecutionResult,
    build_run_tests_tool,
)
from app.tool import BuiltTool, ToolContext, ToolResult, build_tool, tool_context_for
from app.utils.abort_controller import (
    AbortController,
    bridge_cancel_token,
    create_child_abort_controller,
)
from app.utils.messages import Message, ToolCall
from app.workspace.local import LocalWorkspace

# ============================================================================
# Test Fixture Helpers & Tool Factories
# ============================================================================


def _make_write_file_tool(workspace: LocalWorkspace) -> BuiltTool:
    async def _write(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = str(args.get("path") or "")
        content = str(args.get("content") or "")
        workspace.write_file(path, content)
        return ToolResult(content=f"Wrote {len(content)} characters to {path}")

    return build_tool(
        name="WriteFile",
        prompt="Writes file contents to the workspace.",
        call=_write,
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    )


def _make_workspace_test_runner(ws: LocalWorkspace):
    async def _runner(test_path: str, context: ToolContext) -> SuiteExecutionResult:
        cmd = f'PYTHONPATH=. {sys.executable} -m pytest "{test_path}" -vv --tb=short'
        res = ws.execute(cmd)
        return SuiteExecutionResult(
            exit_code=res.exit_code,
            stdout=res.stdout,
            stderr=res.stderr,
        )

    return _runner


class ScriptedModel:
    """Streams a pre-recorded sequence of turns into the loop."""

    def __init__(self, turns: list[list[Message]]) -> None:
        self._turns = list(turns)

    async def __call__(self, state: Any, config: Any) -> AsyncIterator[Message]:
        if not self._turns:
            raise AssertionError("Model received more turns than scripted.")
        for msg in self._turns.pop(0):
            yield msg


# ============================================================================
# 1. Full E2E TDD Cycle: Red -> Green -> Refactor
# ============================================================================

@pytest.mark.anyio
async def test_e2e_full_tdd_red_green_refactor_lifecycle(python_workspace: LocalWorkspace) -> None:
    workspace = python_workspace

    # Prepare tools: the production RunTests, so REFACTOR is judged by real pytest-cov coverage
    write_tool = _make_write_file_tool(workspace)
    run_tests_tool = build_run_tests_tool(None)
    tools = (write_tool, run_tests_tool)

    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
    tool_ctx = tool_context_for(store, tools=tools, workspace=workspace)
    state = initial_loop_state((HumanMessage(content="Implement calculator addition with TDD"),), tool_ctx)
    config = build_run_config("e2e-tdd-run")

    test_content = (
        "import calculator\n\n"
        "def test_add():\n"
        "    assert calculator.add(2, 3) == 5\n"
    )
    impl_content_v1 = (
        "def add(a, b):\n"
        "    return a + b\n"
    )
    impl_content_refactored = (
        '"""Calculator operations module."""\n\n'
        "def add(a: int | float, b: int | float) -> int | float:\n"
        '    """Return sum of two numbers."""\n'
        "    return a + b\n"
    )

    scripted_turns: list[list[Message]] = [
        # Turn 1: Write test (RED)
        [
            AIMessage(
                content="Writing unit test for addition in tests/test_calc.py.",
                tool_calls=[{
                    "name": "WriteFile",
                    "args": {"path": "tests/test_calc.py", "content": test_content},
                    "id": "call_1",
                }],
            )
        ],
        # Turn 2: Run the new tests (any outcome ends RED -> advances to GREEN)
        [
            AIMessage(
                content="Running tests to confirm failure.",
                tool_calls=[{
                    "name": "RunTests",
                    "args": {"test_path": "tests/test_calc.py"},
                    "id": "call_2",
                }],
            )
        ],
        # Turn 3: Write minimal implementation (GREEN)
        [
            AIMessage(
                content="Test failed as expected. Writing minimal implementation.",
                tool_calls=[{
                    "name": "WriteFile",
                    "args": {"path": "calculator.py", "content": impl_content_v1},
                    "id": "call_3",
                }],
            )
        ],
        # Turn 4: Run tests (a pass ends GREEN -> the mandatory REFACTOR)
        [
            AIMessage(
                content="Running tests to verify green phase.",
                tool_calls=[{
                    "name": "RunTests",
                    "args": {"test_path": "tests/test_calc.py"},
                    "id": "call_4",
                }],
            )
        ],
        # Turn 5: Refactor implementation (REFACTOR)
        [
            AIMessage(
                content="Tests pass. Refactoring with type hints and docstrings.",
                tool_calls=[{
                    "name": "WriteFile",
                    "args": {"path": "calculator.py", "content": impl_content_refactored},
                    "id": "call_5",
                }],
            )
        ],
        # Turn 6: Run tests after refactoring (a pass with full coverage ends REFACTOR)
        [
            AIMessage(
                content="Running tests after refactoring.",
                tool_calls=[{
                    "name": "RunTests",
                    "args": {"test_path": "tests/test_calc.py"},
                    "id": "call_6",
                }],
            )
        ],
        # Turn 7: Model claims completion (no tool calls)
        [
            AIMessage(
                content="TDD cycle complete: test written, confirmed failing, implemented, verified, and refactored.",
            )
        ],
    ]

    events_list: list[Any] = []

    async def _noop_compact(s: Any, c: Any) -> CompactionResult:
        return CompactionResult(compacted=False, messages=s.messages)

    stop_hooks = build_stop_hooks_runner()

    deps = LoopDeps(
        call_model=ScriptedModel(scripted_turns),
        compact=_noop_compact,
        uuid=lambda: "uuid-1",
        now=lambda: 123456789.0,
        run_tools=run_tools,
        stop_hooks=stop_hooks,
        emit_event=events_list.append,
    )

    terminated = await drain(run_loop(state, config, deps))

    # Engine completed cleanly because Red and Green phases were verified
    assert terminated.reason == Terminal.COMPLETED

    # Verify final workspace state on disk
    assert workspace.exists("tests/test_calc.py")
    assert workspace.exists("calculator.py")
    assert "Calculator operations module" in workspace.read_file("calculator.py")

    # Verify final authoritative ledger
    final_ledger = store.get().phase_ledger
    assert final_ledger == PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True,
                                       refactor_passed=True, coverage=100.0)


# ============================================================================
# 2. Premature Exit Blocking & Latching Contract
# ============================================================================

@pytest.mark.anyio
async def test_e2e_stop_hook_blocks_premature_exit_and_prevents_infinite_loop(tmp_path: Path) -> None:
    workspace = LocalWorkspace(tmp_path / "workspace")
    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED, tests_written=False)))
    tool_ctx = tool_context_for(store, workspace=workspace)
    state = initial_loop_state((HumanMessage(content="Task"),), tool_ctx)
    config = build_run_config("premature-run")

    # Model attempts to exit twice without running tests
    scripted_turns: list[list[Message]] = [
        [AIMessage(content="I'm all done! Turn 1.")],
        [AIMessage(content="Still done! Turn 2.")],
    ]

    events_list: list[Any] = []

    async def _noop_compact(s: Any, c: Any) -> CompactionResult:
        return CompactionResult(compacted=False, messages=s.messages)

    stop_hooks = build_stop_hooks_runner()

    deps = LoopDeps(
        call_model=ScriptedModel(scripted_turns),
        compact=_noop_compact,
        uuid=lambda: "uuid-2",
        now=lambda: 123456789.0,
        run_tools=run_tools,
        stop_hooks=stop_hooks,
        emit_event=events_list.append,
    )

    terminated = await drain(run_loop(state, config, deps))

    # Both exit attempts were sent back (the hook never gives up); the scripted model then ran
    # out of turns while the hook was still refusing, so the run ends on that model error.
    assert terminated.reason == Terminal.MODEL_ERROR

    reasons = [getattr(e, "reason", None) for e in events_list]
    assert reasons.count(Continue.STOP_HOOK_BLOCKING) == 2
    assert Terminal.STOP_HOOK_PREVENTED not in reasons and Terminal.COMPLETED not in reasons


# ============================================================================
# 3. Streaming Cancellation & Abort Tree Cascade with History Repair
# ============================================================================

@pytest.mark.anyio
async def test_e2e_streaming_cancellation_and_abort_tree_cascade(tmp_path: Path) -> None:
    workspace = LocalWorkspace(tmp_path / "workspace")
    store = AppStateStore(AppState())
    tool_ctx = tool_context_for(store, workspace=workspace)

    # Construct hierarchical 3-controller abort tree
    turn_controller = AbortController()
    batch_controller = create_child_abort_controller(turn_controller)
    tool_controller = create_child_abort_controller(batch_controller)

    # Bridge token
    bridge_cancel_token(tool_ctx.cancel, turn_controller)

    executed_calls: list[str] = []

    async def _slow_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        executed_calls.append("slow_tool")
        # Trigger cancellation mid-stream via the root abort controller
        turn_controller.abort("user_cancelled")
        return ToolResult(content="Slow tool done.")

    async def _second_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        executed_calls.append("second_tool")
        return ToolResult(content="Second tool done.")

    t1 = build_tool(name="SlowTool", prompt="", call=_slow_tool)
    t2 = build_tool(name="SecondTool", prompt="", call=_second_tool)
    tools = (t1, t2)

    tool_calls: tuple[ToolCall, ...] = (
        {"name": "SlowTool", "args": {}, "id": "call_slow"},
        {"name": "SecondTool", "args": {}, "id": "call_second"},
    )
    assistant_msgs: tuple[Message, ...] = (
        AIMessage(content="Running batch", tool_calls=list(tool_calls)),
    )

    state = initial_loop_state(assistant_msgs, tool_ctx)
    config = build_run_config("abort-run")

    results: list[Message] = []
    async for msg in run_tools(tool_calls, assistant_msgs, state, config, tools=tools):
        results.append(msg)

    # 1. Abort signal propagated to root and children
    assert turn_controller.signal.aborted is True
    assert batch_controller.signal.aborted is True
    assert tool_controller.signal.aborted is True

    # 2. CancelToken bridged from controller
    assert tool_ctx.cancel.cancelled is True

    # 3. Only first tool executed
    assert executed_calls == ["slow_tool"]

    # 4. History repair invariant: exactly 2 ToolMessages yielded
    assert len(results) == 2
    assert isinstance(results[0], ToolMessage)
    assert results[0].tool_call_id == "call_slow"
    assert results[0].content == "Slow tool done."

    from app.services.tools.tool_execution import CANCEL_MESSAGE

    assert isinstance(results[1], ToolMessage)
    assert results[1].tool_call_id == "call_second"
    assert results[1].content == CANCEL_MESSAGE
    assert results[1].status == "error"
    assert results[1].additional_kwargs.get("synthetic_repair") is True

    # 5. In-flight tool IDs cleared
    assert len(tool_ctx.in_flight_tool_ids) == 0


# ============================================================================
# 4. Live OpenAI LLM Verification (Conditional on Key Availability)
# ============================================================================

def _get_live_openai_key() -> str | None:
    env_path = Path(".env")
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                val = line.split("=", 1)[1].strip().strip("'\"")
                if val and val != "test-openai-key":
                    return val

    candidate = os.getenv("OPENAI_API_KEY")
    if candidate and candidate != "test-openai-key":
        return candidate
    return None


@pytest.mark.live
@pytest.mark.anyio
async def test_live_llm_invocation_smoke() -> None:
    api_key = _get_live_openai_key()
    if not api_key:
        pytest.skip("No valid OPENAI_API_KEY configured in environment or .env")

    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    model = ChatOpenAI(model="gpt-4o-mini", api_key=SecretStr(api_key))
    response = await model.ainvoke([HumanMessage(content="Say the word 'ANTIGRAVITY_READY' and nothing else.")])
    assert "ANTIGRAVITY" in str(response.content).upper()


@pytest.mark.live
@pytest.mark.anyio
async def test_e2e_live_openai_skill_activation_and_execution(tmp_path) -> None:
    api_key = _get_live_openai_key()
    if not api_key:
        pytest.skip("No valid OPENAI_API_KEY configured in environment or .env")

    from types import SimpleNamespace

    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    from app.query.config import build_run_config
    from app.skills.load_skills_dir import get_skill_registry
    from app.tools.skill_tool.skill_tool import build_skill_tool
    from app.utils.attachments import (
        get_skill_listing_attachments,
        normalize_attachment_for_api,
    )
    from tests.test_loop_engine import FakeModel, make_fake_deps

    # 1. Discover the bundled skills and build the listing the model receives
    registry = get_skill_registry(tmp_path, user_home=tmp_path)
    config = build_run_config("tdd-skill-e2e")
    skill_tool = build_skill_tool(registry, config=config, deps=make_fake_deps(FakeModel([])))
    listing_context = SimpleNamespace(tools=(skill_tool,), agent_id=None, skill_registry=registry)
    (listing,) = get_skill_listing_attachments([], listing_context)
    (skills_section,) = normalize_attachment_for_api(listing)
    assert "tdd-test-design" in skills_section

    # 2. Bind the Skill tool to the live model
    openai_tool_schema = {
        "type": "function",
        "function": {
            "name": skill_tool.name,
            "description": skill_tool.prompt,
            "parameters": skill_tool.input_schema,
        },
    }

    model = ChatOpenAI(model="gpt-4o-mini", api_key=SecretStr(api_key), temperature=0)
    bound_model = model.bind_tools([openai_tool_schema])

    system_prompt = (
        "You are an expert TDD assistant.\n\n"
        f"{skills_section}\n\n"
        "When designing new unit tests, you must execute the appropriate skill first to load its guidelines."
    )
    user_prompt = (
        "I am writing a new test suite in tests/test_validator.py for an email validator function. "
        "Invoke the test design skill to get guidelines for writing clean isolated tests."
    )

    # 3. Model invocation produces a Skill tool call
    ai_msg = await bound_model.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])
    assert isinstance(ai_msg, AIMessage)
    assert len(ai_msg.tool_calls) >= 1

    selected_call = next((tc for tc in ai_msg.tool_calls if tc["name"] == "Skill"), None)
    assert selected_call is not None
    assert selected_call["args"]["skill"] == "tdd-test-design"

    # 4. Execute the Skill tool: the skill's text arrives as a meta message
    store = AppStateStore(AppState())
    ctx = tool_context_for(store)
    tool_result = await skill_tool.call(selected_call["args"], ctx)

    assert tool_result.is_error is False
    assert tool_result.content == "Launching skill: tdd-test-design"
    (skill_message,) = tool_result.new_messages
    assert "TDD Test Design Skill" in skill_message.content

    # 5. Return the result and the skill text to the live model
    tool_msg = ToolMessage(
        content=tool_result.content,
        tool_call_id=selected_call["id"],
    )
    followup_msg = await bound_model.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
        ai_msg,
        tool_msg,
        skill_message,
    ])

    assert isinstance(followup_msg, AIMessage)
    assert len(str(followup_msg.content)) > 50
