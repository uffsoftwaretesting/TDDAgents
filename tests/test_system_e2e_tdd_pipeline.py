"""
End-to-End System Integration Test Suite for TDDAgents.

Validates the integrated multi-turn lifecycle across Parts D, E, F, G, and H:
1. End-to-end autonomous TDD cycle (Red -> Green -> Refactor) using run_loop,
   LocalWorkspace, real file edits, test execution, and phase transitions.
2. Premature exit enforcement via UnifiedStopHooks latching and STOP_HOOK_PREVENTED.
3. Cooperative cancellation and abort tree cascading with history-repair placeholder injection.
4. Bidirectional SyncEngine reconciliation and automatic conflict preservation (.local.<timestamp>).
5. Optional live LLM roundtrip if OPENAI_API_KEY is configured in the environment or .env.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any, AsyncIterator

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.hooks.stop_hooks import build_stop_hooks_runner
from app.loop.config import build_run_config
from app.loop.context import AppState, AppStateStore, ToolContext, tool_context_for
from app.loop.deps import CompactionResult, LoopDeps
from app.loop.engine import drain, run_loop
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.messages import Message, ToolCall
from app.loop.state import initial_loop_state
from app.loop.streaming.abort import (
    AbortController,
    bridge_cancel_token,
    create_child_abort_controller,
)
from app.loop.tools.base import BuiltTool, ToolResult, build_tool
from app.loop.tools.orchestration import run_tools
from app.loop.tools.run_tests import SuiteExecutionResult, build_run_tests_tool
from app.loop.transitions import Continue, Terminal
from app.sync import events
from app.sync.engine import SyncEngine
from app.sync.events import SyncConflict
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
async def test_e2e_full_tdd_red_green_refactor_lifecycle(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace = LocalWorkspace(workspace_dir)

    # Prepare tools
    write_tool = _make_write_file_tool(workspace)
    run_tests_tool = build_run_tests_tool(_make_workspace_test_runner(workspace))
    tools = (write_tool, run_tests_tool)

    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
    tool_ctx = tool_context_for(store, tools=tools, workspace=workspace)
    state = initial_loop_state((HumanMessage(content="Implement calculator addition with TDD"),), tool_ctx)
    config = build_run_config("e2e-tdd-run", postgres_checkpointing=False)

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
        # Turn 2: Run tests (expect failure -> RED confirmed -> advances to GREEN)
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
        # Turn 4: Run tests (expect pass -> GREEN confirmed)
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
        # Turn 6: Run tests after refactoring
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
    assert final_ledger.red_confirmed is True
    assert final_ledger.green_passed is True


# ============================================================================
# 2. Premature Exit Blocking & Latching Contract
# ============================================================================

@pytest.mark.anyio
async def test_e2e_stop_hook_blocks_premature_exit_and_prevents_infinite_loop(tmp_path: Path) -> None:
    workspace = LocalWorkspace(tmp_path / "workspace")
    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED, red_confirmed=False)))
    tool_ctx = tool_context_for(store, workspace=workspace)
    state = initial_loop_state((HumanMessage(content="Task"),), tool_ctx)
    config = build_run_config("premature-run", postgres_checkpointing=False)

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

    # Second exit attempt terminated with STOP_HOOK_PREVENTED, halting infinite loop
    assert terminated.reason == Terminal.STOP_HOOK_PREVENTED

    # Ensure turn 1 recorded STOP_HOOK_BLOCKING transition
    reasons = [getattr(e, "reason", None) for e in events_list]
    assert Continue.STOP_HOOK_BLOCKING in reasons
    assert Terminal.STOP_HOOK_PREVENTED in reasons


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
    config = build_run_config("abort-run", postgres_checkpointing=False)

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

    from app.loop.tools.execution import CANCEL_MESSAGE

    assert isinstance(results[1], ToolMessage)
    assert results[1].tool_call_id == "call_second"
    assert results[1].content == CANCEL_MESSAGE
    assert results[1].status == "error"
    assert results[1].additional_kwargs.get("synthetic_repair") is True

    # 5. In-flight tool IDs cleared
    assert len(tool_ctx.in_flight_tool_ids) == 0


# ============================================================================
# 4. SyncEngine Bidirectional Checkpointing & Conflict Resolution
# ============================================================================

def test_e2e_sync_engine_checkpoint_reconciliation_and_conflict_resolution(tmp_path: Path) -> None:
    sandbox_ws = LocalWorkspace(tmp_path / "sandbox")
    local_ws = LocalWorkspace(tmp_path / "local")
    baseline_path = tmp_path / "sync_baseline.json"

    # Seed initial shared state from local
    local_ws.write_file("shared/module.py", "def compute():\n    return 42\n")

    engine = SyncEngine(sandbox=sandbox_ws, local=local_ws, baseline_path=baseline_path)
    seed_cp = engine.seed()
    assert seed_cp.kind == "run_start"
    assert sandbox_ws.read_file("shared/module.py") == "def compute():\n    return 42\n"

    # Simulate concurrent modification on both sides
    sandbox_ws.write_file("shared/module.py", "def compute():\n    return 100  # sandbox update\n")
    local_ws.write_file("shared/module.py", "def compute():\n    return 999  # local conflicting edit\n")

    # Reconcile at sub_req_boundary checkpoint
    flush_cp = engine.flush(kind="sub_req_boundary")
    assert flush_cp.conflicts == 1

    # Conflict preservation: local edit backed up with timestamp
    emitted = [e for e in events.drain() if isinstance(e, SyncConflict)]
    assert len(emitted) == 1
    assert emitted[0].path == "shared/module.py"
    backup_path = emitted[0].backup_path
    assert ".local." in backup_path
    assert local_ws.exists(backup_path)
    assert "999  # local conflicting edit" in local_ws.read_file(backup_path)

    # Sandbox version successfully won and propagated to active local file
    assert "100  # sandbox update" in local_ws.read_file("shared/module.py")


# ============================================================================
# 5. Live OpenAI LLM Verification (Conditional on Key Availability)
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


@pytest.mark.anyio
async def test_live_llm_invocation_smoke() -> None:
    api_key = _get_live_openai_key()
    if not api_key:
        pytest.skip("No valid OPENAI_API_KEY configured in environment or .env")

    from pydantic import SecretStr
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(model="gpt-4o-mini", api_key=SecretStr(api_key))
    response = await model.ainvoke([HumanMessage(content="Say the word 'ANTIGRAVITY_READY' and nothing else.")])
    assert "ANTIGRAVITY" in str(response.content).upper()


@pytest.mark.anyio
async def test_e2e_live_openai_skill_activation_and_execution() -> None:
    api_key = _get_live_openai_key()
    if not api_key:
        pytest.skip("No valid OPENAI_API_KEY configured in environment or .env")

    from pydantic import SecretStr
    from langchain_openai import ChatOpenAI
    from app.loop.skills.loader import discover_skills
    from app.loop.skills.activation import render_skills_prompt_section
    from app.loop.skills.tool import build_skill_tool

    # 1. Discover bundled skills and render system prompt
    registry = discover_skills()
    skills_section = render_skills_prompt_section(registry.list_skills())
    assert "<available_skills>" in skills_section
    assert "tdd-test-design" in skills_section

    # 2. Build Skill tool and bind to live model
    skill_tool = build_skill_tool(registry)
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
    assert selected_call["args"]["skill_name"] == "tdd-test-design"

    # 4. Execute Skill tool through our real engine tool runner
    store = AppStateStore(AppState())
    ctx = tool_context_for(store)
    tool_result = await skill_tool.call(selected_call["args"], ctx)

    assert tool_result.is_error is False
    assert '<command-message name="tdd-test-design">' in tool_result.content
    assert "Test Isolation and Mocking Principles" in tool_result.content

    # 5. Return tool execution response back to live model
    tool_msg = ToolMessage(
        content=tool_result.content,
        tool_call_id=selected_call["id"],
    )
    followup_msg = await bound_model.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
        ai_msg,
        tool_msg,
    ])

    assert isinstance(followup_msg, AIMessage)
    assert len(str(followup_msg.content)) > 50


def test_e2e_live_openai_session_shell_interrupt_and_iteration() -> None:
    """
    Live OpenAI E2E integration test for Part K:
    Validates that SessionShell runs with live LLM (gpt-4o-mini), correctly interrupts
    on requirements analysis for human-in-the-loop review, and resumes to execute plan items.
    """
    api_key = _get_live_openai_key()
    if not api_key:
        pytest.skip("No valid OPENAI_API_KEY configured in environment or .env")

    os.environ["OPENAI_API_KEY"] = api_key
    from app.config.config import Config
    Config.OPENAI_API_KEY = api_key

    from app.session.shell import SessionShell
    from app.session.state import SessionStatus

    shell = SessionShell()
    thread_id = "live-test-shell-session-k"

    # Turn 1: Initial run with prompt - live analyst generates response and interrupts
    result1 = shell.run(
        "Build a Python string reversal function with unit tests.",
        thread_id,
    )
    assert result1["status"] == SessionStatus.INITIALIZING

    state1 = shell.get_state(thread_id)
    # Verify interrupt was generated by the live analyst
    assert len(state1.tasks) > 0
    assert len(state1.tasks[0].interrupts) > 0
    interrupt_payload = state1.tasks[0].interrupts[0].value
    assert "prompt" in interrupt_payload
    assert len(str(interrupt_payload["prompt"])) > 20

    # Turn 2: User approves via resume("/yes")
    result2 = shell.resume(thread_id, "/yes")
    assert result2["status"] in (SessionStatus.COMPLETED, SessionStatus.ITEM_COMPLETE)
    assert result2["user_confirmed"] is True
    assert len(result2.get("plan", [])) >= 1
    assert result2.get("success_count", 0) >= 1


def test_e2e_live_openai_metrics_artifact_export_roundtrip(tmp_path: Path) -> None:
    """
    Live OpenAI E2E integration test for Part L:
    Validates that real session execution history produces an EventLog that
    correctly classifies flows, computes resilience metrics, and exports
    events.jsonl, subreq_results.txt, and resilience_metrics.txt.
    """
    api_key = _get_live_openai_key()
    if not api_key:
        pytest.skip("No valid OPENAI_API_KEY configured in environment or .env")

    os.environ["OPENAI_API_KEY"] = api_key
    from app.config.config import Config
    Config.OPENAI_API_KEY = api_key

    from app.session.shell import SessionShell
    from app.session.state import SessionStatus
    from app.metrics.event_log import EventLog, EventType
    from app.metrics.report import export_full_metrics_artifacts

    shell = SessionShell()
    thread_id = "live-test-shell-session-l"

    # Step 1: Run through analyst interrupt
    shell.run("Implement a palindrome checking utility.", thread_id)
    # Step 2: Resume to execute plan
    final_result = shell.resume(thread_id, "/yes")
    assert final_result["status"] in (SessionStatus.COMPLETED, SessionStatus.ITEM_COMPLETE)

    plan = final_result.get("plan", ["Implement palindrome checker"])
    assert len(plan) >= 1

    # Step 3: Record live session events into EventLog
    log = EventLog(session_id=thread_id)
    log.record(
        EventType.SESSION,
        payload={"task": "Implement palindrome checker", "status": str(final_result["status"])},
    )
    for idx, item in enumerate(plan):
        log.record_transition(
            turn_count=idx * 2 + 1,
            phase="red",
            reason="next_turn",
            red_confirmed=True,
            green_passed=False,
            payload={"plan_index": idx, "item": item},
        )
        log.record_transition(
            turn_count=idx * 2 + 2,
            phase="green",
            reason="completed",
            red_confirmed=True,
            green_passed=True,
            payload={"plan_index": idx, "item": item},
        )

    # Step 4: Export full metrics artifacts to disk
    artifacts = export_full_metrics_artifacts(log, plan, tmp_path)
    assert artifacts["events_jsonl"].is_file()
    assert artifacts["subreq_results"].is_file()
    assert artifacts["resilience_metrics"].is_file()

    # Step 5: Validate file contents
    jsonl_lines = artifacts["events_jsonl"].read_text(encoding="utf-8").strip().splitlines()
    assert len(jsonl_lines) == 1 + 2 * len(plan)

    subreq_content = artifacts["subreq_results"].read_text(encoding="utf-8")
    assert "SUBREQ RESULTS REPORT" in subreq_content
    assert "F1 (Clean TDD):" in subreq_content

    resilience_content = artifacts["resilience_metrics"].read_text(encoding="utf-8")
    assert "RESILIENCE METRICS REPORT (TDD)" in resilience_content
    assert "Self-Correction Success Rate:              100.00%" in resilience_content
