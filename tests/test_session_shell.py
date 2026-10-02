"""
Unit and integration tests for Part K: Session Shell, Checkpointing, Interrupt, and Plan-Item Iteration.

Covers:
- Part K1: LangGraph checkpointing, MemorySaver, cross-restart state persistence.
- Part K2: Human-in-the-loop analyst interrupt() protocol, clarification cycles, and checklist approval.
- Part K3: Plan generation, sequential plan-item iteration, Red-then-Green ledger invariant validation,
  evaluator advancement, and termination.
- SessionShell sync (run/resume) and async (arun/aresume) APIs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock, patch

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END
import pytest

from app.loop.transitions import Terminal
from app.session.analyst import _DEFAULT_AFFIRMATIVES, node_analyst_interrupt
from app.session.checkpointer import build_checkpointer
from app.session.iteration import (
    anode_execute_plan_item,
    node_evaluator,
    node_execute_plan_item,
    node_plan,
)
from app.session.shell import (
    SessionShell,
    build_session_run_config,
    create_initial_session_state,
    create_session_graph,
    route_after_analyst,
    route_after_evaluator,
    route_after_planner,
)
from app.session.state import PlanItemResult, SessionState, SessionStatus


# ==============================================================================
# Part K1: Checkpointer & State Persistence Tests
# ==============================================================================


class TestCheckpointerFactory:
    """Verifies checkpointer factory initialization and fallback behaviour (Part K1)."""

    def test_default_memory_saver(self) -> None:
        cp = build_checkpointer()
        assert isinstance(cp, MemorySaver)

    def test_explicit_memory_saver_strings(self) -> None:
        for val in (":memory:", ":MEMORY:", "memory", "MEMORY", "", "  ", None):
            cp = build_checkpointer(val)
            assert isinstance(cp, MemorySaver)

    def test_unrecognized_uri_falls_back_to_memory(self) -> None:
        for bad_uri in ("redis://localhost:6379/0", "sqlite:///foo.db", "invalid"):
            cp = build_checkpointer(bad_uri)
            assert isinstance(cp, MemorySaver)

    def test_postgres_saver_success_postgresql_prefix(self) -> None:
        from psycopg.rows import dict_row

        with patch("psycopg.connect") as mock_connect, patch(
            "langgraph.checkpoint.postgres.PostgresSaver"
        ) as mock_pg:
            mock_saver = MagicMock()
            mock_pg.return_value = mock_saver
            uri = "postgresql://user:pass@localhost:5432/testdb"
            cp = build_checkpointer(uri)
            assert cp is mock_saver
            mock_connect.assert_called_once_with(uri, autocommit=True, row_factory=dict_row)
            mock_saver.setup.assert_called_once()

    def test_postgres_saver_success_postgres_prefix(self) -> None:
        from psycopg.rows import dict_row

        with patch("psycopg.connect") as mock_connect, patch(
            "langgraph.checkpoint.postgres.PostgresSaver"
        ) as mock_pg:
            mock_saver = MagicMock()
            mock_pg.return_value = mock_saver
            uri = "postgres://user:pass@localhost:5432/testdb"
            cp = build_checkpointer(uri)
            assert cp is mock_saver
            mock_connect.assert_called_once_with(uri, autocommit=True, row_factory=dict_row)
            mock_saver.setup.assert_called_once()

    def test_postgres_saver_failure_on_connect_falls_back_to_memory(self) -> None:
        with patch("psycopg.connect", side_effect=Exception("Connection refused")):
            cp = build_checkpointer("postgresql://user:pass@localhost:5432/testdb")
            assert isinstance(cp, MemorySaver)

    def test_postgres_saver_failure_on_setup_falls_back_to_memory(self) -> None:
        with patch("psycopg.connect"), patch(
            "langgraph.checkpoint.postgres.PostgresSaver"
        ) as mock_pg:
            mock_saver = MagicMock()
            mock_saver.setup.side_effect = RuntimeError("Setup failed")
            mock_pg.return_value = mock_saver
            cp = build_checkpointer("postgresql://user:pass@localhost:5432/testdb")
            assert isinstance(cp, MemorySaver)

    def test_checkpointer_logging_success(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch("psycopg.connect"), patch("langgraph.checkpoint.postgres.PostgresSaver") as mock_pg:
            mock_pg.return_value = MagicMock()
            with caplog.at_level(logging.INFO):
                build_checkpointer("postgresql://user:pass@myhost:5432/testdb")
            assert "Configured PostgresSaver checkpointer for URI: myhost:5432/testdb" in caplog.text

    def test_checkpointer_logging_connect_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch("psycopg.connect", side_effect=Exception("Connection refused")):
            with caplog.at_level(logging.WARNING):
                build_checkpointer("postgresql://user:pass@myhost:5432/testdb")
            msg = "Failed to initialize PostgresSaver (Connection refused). Falling back to MemorySaver."
            assert msg in caplog.text

    def test_checkpointer_logging_unrecognized_uri(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            build_checkpointer("redis://localhost:6379/0")
        assert "Unrecognized checkpointer URI 'redis://localhost:6379/0'. Falling back to MemorySaver." in caplog.text


class TestCrossRestartPersistence:
    """Verifies cross-restart session persistence under the same thread_id (Part K1)."""

    def test_cross_restart_resume_with_shared_checkpointer(self) -> None:
        shared_cp = MemorySaver()
        thread_id = "restart-test-thread-101"

        def mock_analyzer(user_input: str, history: str) -> dict[str, Any]:
            return {
                "response": "Checklist: 1. Setup 2. Implement. Proceed?",
                "needs_clarification": False,
                "has_checklist": True,
            }

        def mock_planner(spec: str) -> list[str]:
            return ["SubTask A", "SubTask B"]

        def mock_runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            return PlanItemResult(
                index=idx,
                sub_requirement=sub_req,
                status="success",
                terminal_reason="completed",
                red_confirmed=True,
                green_passed=True,
            )

        # 1. Instance A starts session and hits interrupt()
        shell_a = SessionShell(
            checkpointer=shared_cp,
            analyzer_fn=mock_analyzer,
            planner_fn=mock_planner,
            loop_runner=mock_runner,
        )
        assert shell_a.checkpointer is shared_cp
        assert shell_a.graph is not None

        shell_a.run("Build a payment gateway", thread_id)
        state_snap_a = shell_a.get_state(thread_id)
        assert state_snap_a.values["session_id"] == thread_id
        assert bool(state_snap_a.tasks and state_snap_a.tasks[0].interrupts)

        # 2. Simulate process crash / restart: Create completely new instance B with shared_cp
        shell_b = SessionShell(
            checkpointer=shared_cp,
            analyzer_fn=mock_analyzer,
            planner_fn=mock_planner,
            loop_runner=mock_runner,
        )
        state_snap_b = shell_b.get_state(thread_id)
        assert state_snap_b.values["session_id"] == thread_id
        assert state_snap_b.values["user_confirmed"] is False

        # 3. Resume from Instance B
        result_b = shell_b.resume(thread_id, "/yes")
        assert result_b["status"] == SessionStatus.COMPLETED
        assert result_b["user_confirmed"] is True
        assert result_b["success_count"] == 2
        assert len(result_b["subreq_results"]) == 2


# ==============================================================================
# Part K2: Analyst Interrupt Protocol Tests
# ==============================================================================


class TestAnalystInterruptNode:
    """Verifies human-in-the-loop analyst interrupt() and approval behavior (Part K2)."""

    def test_analyst_already_confirmed_early_return(self) -> None:
        state: SessionState = {
            "user_confirmed": True,
            "session_id": "test-1",
        }
        out = node_analyst_interrupt(state)
        assert out["status"] == SessionStatus.ANALYSIS_COMPLETE
        assert out["user_confirmed"] is True

    def test_analyst_error_returns_failed(self) -> None:
        def crashing_analyzer(user_input: str, history: str) -> dict[str, Any]:
            raise RuntimeError("LLM exploded")

        state: SessionState = {
            "user_input": "Hello",
            "session_id": "test-err",
        }
        out = node_analyst_interrupt(state, analyzer_fn=crashing_analyzer)
        assert out["status"] == SessionStatus.FAILED
        assert out["audit_log"] == ["Analyst error: LLM exploded"]

    def test_analyst_direct_acceptance_without_clarification(self) -> None:
        def direct_analyzer(user_input: str, history: str) -> dict[str, Any]:
            assert user_input == "Full requirements specification here"
            assert history == ""
            return {
                "response": "Detailed complete specification",
                "needs_clarification": False,
                "has_checklist": False,
            }

        state: SessionState = {
            "user_input": "Full requirements specification here",
            "interaction_count": 0,
        }
        out = node_analyst_interrupt(state, analyzer_fn=direct_analyzer)
        assert out["status"] == SessionStatus.ANALYSIS_COMPLETE
        assert out["user_confirmed"] is True
        assert out["has_checklist"] is True
        assert out["needs_clarification"] is False
        assert out["specification"] == "Detailed complete specification"
        assert out["current_response"] == "Detailed complete specification"
        assert out["interaction_count"] == 1
        assert "[User]: Full requirements specification here" in out["requirements"]

    def test_interaction_limit_forces_checklist(self) -> None:
        captured_interrupt: dict[str, Any] = {}

        def mock_interrupt(payload: dict[str, Any]) -> str:
            captured_interrupt.update(payload)
            return "/yes"

        def vague_analyzer(user_input: str, history: str) -> dict[str, Any]:
            return {
                "response": "Still vague...",
                "needs_clarification": True,
                "has_checklist": False,
            }

        state: SessionState = {
            "user_input": "More details",
            "interaction_count": 4,  # Next will be 5
        }

        with patch("app.session.analyst.interrupt", side_effect=mock_interrupt):
            out = node_analyst_interrupt(state, analyzer_fn=vague_analyzer)

        assert captured_interrupt["has_checklist"] is True
        assert captured_interrupt["needs_clarification"] is False
        assert captured_interrupt["type"] == "checklist_approval"
        assert captured_interrupt["interaction_count"] == 5
        assert out["user_confirmed"] is True
        assert out["status"] == SessionStatus.ANALYSIS_COMPLETE
        assert out["interaction_count"] == 5
        expected_spec = "Still vague...\n\nRequirements defined. Here is the final Checklist. May we proceed?"
        assert out["specification"] == expected_spec
        assert out["current_response"] == expected_spec
        assert out["user_input"] == "/yes"
        assert out["needs_clarification"] is False
        assert out["has_checklist"] is True
        assert "[User]: More details" in out["requirements"]
        assert "[Analyst]: Still vague..." in out["requirements"]
        assert out["conversation_history"] == out["requirements"]

    def test_analyst_clarification_cycle_preserves_conversation_history(self) -> None:
        captured_interrupt: dict[str, Any] = {}

        def mock_interrupt(payload: dict[str, Any]) -> str:
            captured_interrupt.update(payload)
            return "Use PostgreSQL and Redis"

        def asking_analyzer(user_input: str, history: str) -> dict[str, Any]:
            assert user_input == "I need a fast cache"
            assert history == "[User]: Previous query\n[Analyst]: Previous answer"
            return {
                "response": "Which database would you prefer?",
                "needs_clarification": True,
                "has_checklist": False,
            }

        state: SessionState = {
            "user_input": "  I need a fast cache  ",
            "conversation_history": "[User]: Previous query\n[Analyst]: Previous answer",
            "interaction_count": 1,
        }

        with patch("app.session.analyst.interrupt", side_effect=mock_interrupt):
            out = node_analyst_interrupt(state, analyzer_fn=asking_analyzer)

        assert captured_interrupt["type"] == "clarification"
        assert captured_interrupt["has_checklist"] is False
        assert captured_interrupt["needs_clarification"] is True
        assert captured_interrupt["prompt"] == "Which database would you prefer?"
        assert captured_interrupt["interaction_count"] == 2

        assert out["status"] == SessionStatus.AWAITING_INPUT
        assert out["user_confirmed"] is False
        assert out["needs_clarification"] is True
        assert out["has_checklist"] is False
        assert out["user_input"] == "Use PostgreSQL and Redis"
        assert out["interaction_count"] == 2
        expected_history = (
            "[User]: Previous query\n[Analyst]: Previous answer\n\n"
            "[User]: I need a fast cache\n[Analyst]: Which database would you prefer?"
        )
        assert out["conversation_history"] == expected_history
        assert out["requirements"] == expected_history

    def test_analyst_omitted_interaction_count_defaults_to_one(self) -> None:
        def simple_analyzer(user_input: str, history: str) -> dict[str, Any]:
            return {"response": "Ready", "needs_clarification": False, "has_checklist": False}

        state: SessionState = {"user_input": "Do task"}
        out = node_analyst_interrupt(state, analyzer_fn=simple_analyzer)
        assert out["interaction_count"] == 1

    def test_analyst_logging_direct_acceptance(self, caplog: pytest.LogCaptureFixture) -> None:
        def analyzer(u: str, h: str) -> dict[str, Any]:
            return {"response": "All good", "needs_clarification": False, "has_checklist": False}

        with caplog.at_level(logging.INFO):
            out = node_analyst_interrupt({"user_input": "Full requirements"}, analyzer_fn=analyzer)
        assert "Analyst accepted requirements directly without clarification." in caplog.text
        assert out["conversation_history"] == "[User]: Full requirements\n[Analyst]: All good"

    def test_analyst_logging_on_error(self, caplog: pytest.LogCaptureFixture) -> None:
        def crashing(u: str, h: str) -> dict[str, Any]:
            raise RuntimeError("Explosion")

        with caplog.at_level(logging.ERROR):
            node_analyst_interrupt({"user_input": "Task"}, analyzer_fn=crashing)
        assert "Analyst execution error: Explosion" in caplog.text

    def test_analyst_logging_on_resume(self, caplog: pytest.LogCaptureFixture) -> None:
        def analyzer(u: str, h: str) -> dict[str, Any]:
            return {"response": "Which one?", "needs_clarification": True, "has_checklist": False}

        with patch("app.session.analyst.interrupt", return_value="Option B"):
            with caplog.at_level(logging.INFO):
                node_analyst_interrupt({"user_input": "Start"}, analyzer_fn=analyzer)
        assert "Resumed from interrupt with user feedback: 'Option B'" in caplog.text

    def test_analyst_omitted_user_input_defaults_to_empty(self) -> None:
        captured_input = None

        def analyzer(u: str, h: str) -> dict[str, Any]:
            nonlocal captured_input
            captured_input = u
            return {"response": "Ready", "needs_clarification": False, "has_checklist": False}

        node_analyst_interrupt({}, analyzer_fn=analyzer)
        assert captured_input == ""

    def test_analyst_checklist_rejection_cycles_back(self) -> None:
        def mock_interrupt(payload: dict[str, Any]) -> str:
            return "No, add authentication first"

        def checklist_analyzer(user_input: str, history: str) -> dict[str, Any]:
            return {
                "response": "Checklist: 1. Setup API. Proceed?",
                "needs_clarification": False,
                "has_checklist": True,
            }

        state: SessionState = {
            "user_input": "Setup API",
            "interaction_count": 1,
        }

        with patch("app.session.analyst.interrupt", side_effect=mock_interrupt):
            out = node_analyst_interrupt(state, analyzer_fn=checklist_analyzer)

        assert out["status"] == SessionStatus.AWAITING_INPUT
        assert out["user_confirmed"] is False
        assert out["needs_clarification"] is True
        assert out["has_checklist"] is False
        assert out["user_input"] == "No, add authentication first"
        assert out["interaction_count"] == 2

    def test_default_affirmatives_membership(self) -> None:
        for aff in ("yes", "YES", "/yes", "confirm", "proceed", "y", "approved", "ok", "  proceed  "):
            assert aff.strip().lower() in _DEFAULT_AFFIRMATIVES

    def test_default_affirmatives_rejections(self) -> None:
        for rej in ("no", "NO", "wait", "cancel", "stop", "reject"):
            assert rej.strip().lower() not in _DEFAULT_AFFIRMATIVES


# ==============================================================================
# Part K3: Planner, Executor, and Evaluator Tests
# ==============================================================================


class TestPlannerNode:
    """Verifies planner node behavior and error handling (Part K3)."""

    def test_planner_empty_specification_with_session_id(self) -> None:
        state: SessionState = {"session_id": "my-sess-1", "specification": "", "requirements": "", "user_input": ""}
        out = node_plan(state)
        assert out["status"] == SessionStatus.PLAN_FAILED
        assert out["plan"] == []
        assert out["audit_log"] == ["[my-sess-1] Planning failed: empty specification."]

    def test_planner_empty_specification_without_session_id(self) -> None:
        state: SessionState = {"specification": "   ", "requirements": "", "user_input": ""}
        out = node_plan(state)
        assert out["status"] == SessionStatus.PLAN_FAILED
        assert out["plan"] == []
        assert out["audit_log"] == ["[session] Planning failed: empty specification."]

    def test_planner_fallback_to_requirements_when_spec_empty(self) -> None:
        def capturing_planner(spec: str) -> list[str]:
            assert spec == "Build authentication"
            return ["Item 1"]

        state: SessionState = {"specification": "", "requirements": "Build authentication"}
        out = node_plan(state, planner_fn=capturing_planner)
        assert out["status"] == SessionStatus.EXECUTING_PLAN_ITEM
        assert out["plan"] == ["Item 1"]

    def test_planner_fallback_to_user_input_when_spec_and_reqs_empty(self) -> None:
        def capturing_planner(spec: str) -> list[str]:
            assert spec == "Build database"
            return ["Item DB"]

        state: SessionState = {"specification": "", "requirements": "", "user_input": "  Build database  "}
        out = node_plan(state, planner_fn=capturing_planner)
        assert out["status"] == SessionStatus.EXECUTING_PLAN_ITEM
        assert out["plan"] == ["Item DB"]

    def test_planner_exception_fails(self) -> None:
        def crashing_planner(spec: str) -> list[str]:
            raise ValueError("Planner timeout")

        state: SessionState = {"specification": "Build app"}
        out = node_plan(state, planner_fn=crashing_planner)
        assert out["status"] == SessionStatus.PLAN_FAILED
        assert out["audit_log"] == ["Planner error: Planner timeout"]
        assert out["plan"] == []

    def test_planner_empty_list_fails(self) -> None:
        state: SessionState = {"specification": "Build app"}
        out = node_plan(state, planner_fn=lambda s: [])
        assert out["status"] == SessionStatus.PLAN_FAILED
        assert out["audit_log"] == ["[Planner] Planner returned an empty plan."]
        assert out["plan"] == []

    def test_planner_successful_plan(self) -> None:
        items = ["Step 1: Core", "Step 2: CLI", "Step 3: Docs"]
        state: SessionState = {"specification": "Build complete app"}
        out = node_plan(state, planner_fn=lambda s: items)
        assert out["status"] == SessionStatus.EXECUTING_PLAN_ITEM
        assert out["plan"] == items
        assert out["plan_index"] == 0
        assert out["current_sub_req"] == "Step 1: Core"
        assert out["subreq_results"] == []
        assert out["success_count"] == 0
        assert out["failure_count"] == 0
        expected_log = "[Planner] Generated plan with 3 items:\n1. Step 1: Core\n2. Step 2: CLI\n3. Step 3: Docs"
        assert out["audit_log"] == [expected_log]

    def test_planner_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            node_plan({"specification": "Build app"}, planner_fn=lambda s: ["A", "B"])
        assert "Generated plan with 2 sub-requirements." in caplog.text

    def test_planner_error_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        def crashing(s: str) -> list[str]:
            raise RuntimeError("Planner dead")

        with caplog.at_level(logging.ERROR):
            node_plan({"specification": "Build app"}, planner_fn=crashing)
        assert "Planner execution error: Planner dead" in caplog.text


class TestPlanItemExecutor:
    """Verifies plan item execution, ledger contract, and sync/async handling (Part K3)."""

    def test_executor_completed_when_index_exceeds_plan(self) -> None:
        state: SessionState = {"plan": ["A", "B"], "plan_index": 2}
        out = node_execute_plan_item(state)
        assert out["status"] == SessionStatus.COMPLETED

    def test_executor_completed_when_plan_is_empty(self) -> None:
        state: SessionState = {"plan": [], "plan_index": 0}
        out = node_execute_plan_item(state)
        assert out["status"] == SessionStatus.COMPLETED

    def test_executor_default_stub_success_preserves_history(self) -> None:
        state: SessionState = {
            "plan": ["Task 1"],
            "plan_index": 0,
            "success_count": 2,
            "failure_count": 1,
            "audit_log": ["[Init] Started"],
            "subreq_results": [{"prev": 1}],
        }
        out = node_execute_plan_item(state)
        assert out["status"] == SessionStatus.ITEM_COMPLETE
        assert out["success_count"] == 3
        assert out["failure_count"] == 1
        assert len(out["subreq_results"]) == 2
        assert out["subreq_results"][0] == {"prev": 1}
        assert out["subreq_results"][1]["status"] == "success"
        assert out["subreq_results"][1]["index"] == 0
        assert out["subreq_results"][1]["sub_requirement"] == "Task 1"
        assert out["subreq_results"][1]["red_confirmed"] is True
        assert out["subreq_results"][1]["green_passed"] is True
        assert out["audit_log"] == ["[Init] Started", "[Evaluator] Completed item 1/1: 'Task 1'."]

    def test_executor_runner_invariant_failure_red_not_confirmed(self) -> None:
        def non_red_runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            return PlanItemResult(
                index=idx,
                sub_requirement=sub_req,
                status="success",
                terminal_reason="completed",
                red_confirmed=False,  # Violated red ledger contract!
                green_passed=True,
            )

        state: SessionState = {"plan": ["Task 1"], "plan_index": 0, "audit_log": ["[Init]"]}
        out = node_execute_plan_item(state, loop_runner=non_red_runner)
        assert out["status"] == SessionStatus.ITEM_FAILED
        assert out["failure_count"] == 1
        assert out["success_count"] == 0
        expected_log = "[Evaluator] Failed item 1/1: 'Task 1': Invariant failure (red=False, green=True)"
        assert out["audit_log"] == ["[Init]", expected_log]

    def test_executor_runner_invariant_failure_green_not_passed(self) -> None:
        def non_green_runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            return PlanItemResult(
                index=idx,
                sub_requirement=sub_req,
                status="success",
                terminal_reason="completed",
                red_confirmed=True,
                green_passed=False,  # Violated green ledger contract!
            )

        state: SessionState = {"plan": ["Task 1"], "plan_index": 0, "audit_log": ["[Init]"]}
        out = node_execute_plan_item(state, loop_runner=non_green_runner)
        assert out["status"] == SessionStatus.ITEM_FAILED
        assert out["failure_count"] == 1
        expected_log = "[Evaluator] Failed item 1/1: 'Task 1': Invariant failure (red=True, green=False)"
        assert out["audit_log"] == ["[Init]", expected_log]

    def test_executor_runner_non_completed_terminal(self) -> None:
        def stopped_runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            return PlanItemResult(
                index=idx,
                sub_requirement=sub_req,
                status="failed",
                terminal_reason=Terminal.STOP_HOOK_PREVENTED.value,
                red_confirmed=True,
                green_passed=True,
                error_message="Stop hook blocked execution",
            )

        state: SessionState = {"plan": ["Task 1"], "plan_index": 0, "audit_log": ["[Init]"]}
        out = node_execute_plan_item(state, loop_runner=stopped_runner)
        assert out["status"] == SessionStatus.ITEM_FAILED
        assert out["failure_count"] == 1
        assert out["audit_log"] == ["[Init]", "[Evaluator] Failed item 1/1: 'Task 1': Stop hook blocked execution"]

    def test_executor_runner_raises_exception(self) -> None:
        def broken_runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            raise RuntimeError("Docker sandbox unreachable")

        state: SessionState = {"plan": ["Task 1"], "plan_index": 0}
        out = node_execute_plan_item(state, loop_runner=broken_runner)
        assert out["status"] == SessionStatus.ITEM_FAILED
        assert out["failure_count"] == 1
        assert out["subreq_results"][0]["error_message"] == "Docker sandbox unreachable"
        assert out["subreq_results"][0]["terminal_reason"] == "error"
        assert out["subreq_results"][0]["red_confirmed"] is False
        assert out["subreq_results"][0]["green_passed"] is False

    def test_executor_runner_returns_invalid_type(self) -> None:
        def bad_type_runner(sub_req: str, idx: int, state: SessionState) -> Any:
            return {"some": "dictionary"}

        state: SessionState = {"plan": ["Task 1"], "plan_index": 0}
        out = node_execute_plan_item(state, loop_runner=bad_type_runner)
        assert out["status"] == SessionStatus.ITEM_FAILED
        assert "loop_runner must return PlanItemResult" in out["subreq_results"][0]["error_message"]

    def test_sync_executor_calling_async_runner(self) -> None:
        async def async_runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            await asyncio.sleep(0.001)
            return PlanItemResult(
                index=idx,
                sub_requirement=sub_req,
                status="success",
                terminal_reason="completed",
                red_confirmed=True,
                green_passed=True,
            )

        state: SessionState = {"plan": ["Task 1"], "plan_index": 0}
        out = node_execute_plan_item(state, loop_runner=async_runner)
        assert out["status"] == SessionStatus.ITEM_COMPLETE
        assert out["success_count"] == 1

    def test_async_executor_calling_async_runner(self) -> None:
        async def async_runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            await asyncio.sleep(0.001)
            return PlanItemResult(
                index=idx,
                sub_requirement=sub_req,
                status="success",
                terminal_reason="completed",
                red_confirmed=True,
                green_passed=True,
            )

        async def _test() -> None:
            state: SessionState = {"plan": ["Task 1"], "plan_index": 0}
            out = await anode_execute_plan_item(state, loop_runner=async_runner)
            assert out["status"] == SessionStatus.ITEM_COMPLETE
            assert out["success_count"] == 1

        asyncio.run(_test())

    def test_async_executor_calling_sync_runner(self) -> None:
        def sync_runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            return PlanItemResult(
                index=idx,
                sub_requirement=sub_req,
                status="success",
                terminal_reason="completed",
                red_confirmed=True,
                green_passed=True,
            )

        async def _test() -> None:
            state: SessionState = {"plan": ["Task 1"], "plan_index": 0}
            out = await anode_execute_plan_item(state, loop_runner=sync_runner)
            assert out["status"] == SessionStatus.ITEM_COMPLETE
            assert out["success_count"] == 1

        asyncio.run(_test())

    def test_async_executor_completed_when_index_exceeds_plan(self) -> None:
        async def _test() -> None:
            state: SessionState = {"plan": ["A"], "plan_index": 1}
            out = await anode_execute_plan_item(state)
            assert out["status"] == SessionStatus.COMPLETED

        asyncio.run(_test())

    def test_async_executor_default_stub(self) -> None:
        async def _test() -> None:
            state: SessionState = {"plan": ["A"], "plan_index": 0}
            out = await anode_execute_plan_item(state)
            assert out["status"] == SessionStatus.ITEM_COMPLETE
            assert out["success_count"] == 1

        asyncio.run(_test())

    def test_executor_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            node_execute_plan_item({"plan": ["Subtask 1"], "plan_index": 0})
        assert "Executing plan item 1/1: 'Subtask 1'" in caplog.text

    def test_executor_error_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        def crashing(req: str, idx: int, state: SessionState) -> PlanItemResult:
            raise RuntimeError("Sandbox down")

        with caplog.at_level(logging.ERROR):
            node_execute_plan_item({"plan": ["Subtask 1"], "plan_index": 0}, loop_runner=crashing)
        assert "Error executing plan item 'Subtask 1': Sandbox down" in caplog.text


class TestEvaluatorNode:
    """Verifies progression to next plan item or final completion (Part K3)."""

    def test_evaluator_advances_to_next_item(self) -> None:
        state: SessionState = {
            "plan": ["Item 1", "Item 2", "Item 3"],
            "plan_index": 0,
            "success_count": 1,
            "failure_count": 0,
            "audit_log": ["[Init] started"],
        }
        out = node_evaluator(state)
        assert out["plan_index"] == 1
        assert out["current_sub_req"] == "Item 2"
        assert out["status"] == SessionStatus.EXECUTING_PLAN_ITEM
        assert out["audit_log"] == ["[Init] started", "[Evaluator] Advancing to item 2/3: 'Item 2'."]

    def test_evaluator_finishes_with_completed_on_zero_failures(self) -> None:
        state: SessionState = {
            "plan": ["Item 1", "Item 2"],
            "plan_index": 1,  # last item
            "success_count": 2,
            "failure_count": 0,
            "audit_log": ["[Init] started"],
        }
        out = node_evaluator(state)
        assert out["status"] == SessionStatus.COMPLETED
        expected_log = "[Evaluator] Plan finished. Status: completed (Success=2, Failed=0)."
        assert out["audit_log"] == ["[Init] started", expected_log]

    def test_evaluator_finishes_with_failed_on_failures_present(self) -> None:
        state: SessionState = {
            "plan": ["Item 1", "Item 2"],
            "plan_index": 1,  # last item
            "success_count": 1,
            "failure_count": 1,
            "audit_log": ["[Init] started"],
        }
        out = node_evaluator(state)
        assert out["status"] == SessionStatus.FAILED
        expected_log = "[Evaluator] Plan finished. Status: failed (Success=1, Failed=1)."
        assert out["audit_log"] == ["[Init] started", expected_log]


# ==============================================================================
# Routers and Initial State Unit Tests
# ==============================================================================


class TestRoutersAndState:
    """Verifies router functions and initial state builder."""

    def test_create_initial_session_state_all_fields(self) -> None:
        s = create_initial_session_state("thread-abc", "input text", "spec text")
        assert s["session_id"] == "thread-abc"
        assert s["user_input"] == "input text"
        assert s["specification"] == "spec text"
        assert s["requirements"] == ""
        assert s["conversation_history"] == ""
        assert s["needs_clarification"] is False
        assert s["has_checklist"] is False
        assert s["user_confirmed"] is False
        assert s["interaction_count"] == 0
        assert s["plan"] == []
        assert s["plan_index"] == 0
        assert s["subreq_results"] == []
        assert s["status"] == SessionStatus.INITIALIZING
        assert s["audit_log"] == ["[Session] Started thread 'thread-abc'."]
        assert s["success_count"] == 0
        assert s["failure_count"] == 0

    def test_route_after_analyst(self) -> None:
        assert route_after_analyst({"status": SessionStatus.ANALYSIS_COMPLETE}) == "planner"
        assert route_after_analyst({"status": SessionStatus.AWAITING_INPUT}) == "analyst"
        assert route_after_analyst({"status": SessionStatus.FAILED}) == END
        assert route_after_analyst({"status": SessionStatus.PLAN_FAILED}) == END
        assert route_after_analyst({"status": SessionStatus.INITIALIZING}) == "planner"
        assert route_after_analyst({}) == "planner"

    def test_route_after_planner(self) -> None:
        # 1. PLAN_FAILED with non-empty plan -> END
        assert route_after_planner({"status": SessionStatus.PLAN_FAILED, "plan": ["Item 1"]}) == END
        # 2. PLAN_FAILED with empty plan -> END
        assert route_after_planner({"status": SessionStatus.PLAN_FAILED, "plan": []}) == END
        # 3. Not PLAN_FAILED, but empty plan -> END
        assert route_after_planner({"status": SessionStatus.EXECUTING_PLAN_ITEM, "plan": []}) == END
        # 4. Normal plan -> execute_plan_item
        res = route_after_planner({"status": SessionStatus.EXECUTING_PLAN_ITEM, "plan": ["Item 1"]})
        assert res == "execute_plan_item"
        assert route_after_planner({}) == END

    def test_route_after_evaluator(self) -> None:
        assert route_after_evaluator({"status": SessionStatus.EXECUTING_PLAN_ITEM}) == "execute_plan_item"
        assert route_after_evaluator({"status": SessionStatus.COMPLETED}) == END
        assert route_after_evaluator({"status": SessionStatus.FAILED}) == END
        assert route_after_evaluator({}) == END

    def test_create_initial_session_state_default_specification(self) -> None:
        s = create_initial_session_state("thread-1", "user-in")
        assert s["specification"] == ""
        assert s["user_input"] == "user-in"
        assert s["session_id"] == "thread-1"

    def test_create_session_graph_default_checkpointer(self) -> None:
        from langgraph.checkpoint.base import BaseCheckpointSaver

        g = create_session_graph()
        assert g.checkpointer is not None
        assert isinstance(g.checkpointer, BaseCheckpointSaver)

    def test_route_after_planner_missing_plan_key(self) -> None:
        assert route_after_planner({"status": SessionStatus.EXECUTING_PLAN_ITEM}) == END

    def test_session_shell_run_without_specification(self) -> None:
        def analyzer(u: str, h: str) -> dict[str, Any]:
            return {"response": "spec_out", "needs_clarification": False, "has_checklist": False}

        shell = SessionShell(
            analyzer_fn=analyzer,
            planner_fn=lambda s: ["Step 1"],
        )
        thread_id = "test-no-spec-sync"
        shell.run("My task", thread_id)
        snap = shell.get_state(thread_id)
        assert snap.values["user_input"] == "My task"
        assert snap.values["session_id"] == thread_id

    def test_session_shell_arun_with_and_without_specification(self) -> None:
        def analyzer(u: str, h: str) -> dict[str, Any]:
            return {"response": "spec_out", "needs_clarification": False, "has_checklist": False}

        shell = SessionShell(
            analyzer_fn=analyzer,
            planner_fn=lambda s: ["Step 1"],
        )

        async def _test() -> None:
            # 1. Without spec
            await shell.arun("My async task", "test-no-spec-async")
            snap1 = shell.get_state("test-no-spec-async")
            assert snap1.values["user_input"] == "My async task"
            assert snap1.values["session_id"] == "test-no-spec-async"

            # 2. With spec
            await shell.arun("My async task", "test-with-spec-async", specification="Custom spec")
            snap2 = shell.get_state("test-with-spec-async")
            assert snap2.values["session_id"] == "test-with-spec-async"

        asyncio.run(_test())

    def test_build_session_run_config(self) -> None:
        cfg = build_session_run_config("test-thread-42")
        assert cfg["configurable"]["thread_id"] == "test-thread-42"
        assert cfg["recursion_limit"] == 200


# ==============================================================================
# Full Session Flow & Routing Tests (K1 + K2 + K3)
# ==============================================================================


class TestFullSessionFlow:
    """Verifies the complete end-to-end SessionShell flow across all sub-components."""

    def test_full_session_sync_happy_path(self) -> None:
        items = ["Step 1", "Step 2"]

        def mock_analyzer(user_input: str, history: str) -> dict[str, Any]:
            return {
                "response": "Here is the checklist:\n1. Step 1\n2. Step 2\nMay we proceed?",
                "needs_clarification": False,
                "has_checklist": True,
            }

        def mock_planner(spec: str) -> list[str]:
            return items

        def mock_loop(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            return PlanItemResult(
                index=idx,
                sub_requirement=sub_req,
                status="success",
                terminal_reason="completed",
                red_confirmed=True,
                green_passed=True,
            )

        shell = SessionShell(
            analyzer_fn=mock_analyzer,
            planner_fn=mock_planner,
            loop_runner=mock_loop,
        )

        thread_id = "test-sync-happy"
        # Turn 1: Starts session, analyzes, presents checklist and interrupts
        shell.run("Implement features", thread_id, specification="Spec doc")
        snap1 = shell.get_state(thread_id)
        assert snap1.values["status"] == SessionStatus.INITIALIZING
        assert len(snap1.tasks) > 0
        assert len(snap1.tasks[0].interrupts) > 0
        interrupt_payload = snap1.tasks[0].interrupts[0].value
        assert interrupt_payload["has_checklist"] is True

        # Turn 2: User approves via resume("/yes")
        result = shell.resume(thread_id, "/yes")
        assert result["status"] == SessionStatus.COMPLETED
        assert result["user_confirmed"] is True
        assert result["success_count"] == 2
        assert result["failure_count"] == 0
        assert len(result["subreq_results"]) == 2

    def test_full_session_async_happy_path(self) -> None:
        items = ["SubTask 1", "SubTask 2", "SubTask 3"]

        def mock_analyzer(user_input: str, history: str) -> dict[str, Any]:
            return {
                "response": "Checklist ready. Proceed?",
                "needs_clarification": False,
                "has_checklist": True,
            }

        def mock_planner(spec: str) -> list[str]:
            return items

        async def mock_async_loop(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            await asyncio.sleep(0.001)
            return PlanItemResult(
                index=idx,
                sub_requirement=sub_req,
                status="success",
                terminal_reason="completed",
                red_confirmed=True,
                green_passed=True,
            )

        shell = SessionShell(
            analyzer_fn=mock_analyzer,
            planner_fn=mock_planner,
            loop_runner=mock_async_loop,
        )

        async def _test() -> None:
            thread_id = "test-async-happy"
            await shell.arun("Implement microservice", thread_id)
            snap1 = shell.get_state(thread_id)
            assert len(snap1.tasks[0].interrupts) > 0

            result = await shell.aresume(thread_id, "approved")
            assert result["status"] == SessionStatus.COMPLETED
            assert result["success_count"] == 3
            assert len(result["subreq_results"]) == 3

        asyncio.run(_test())

    def test_full_session_clarification_before_approval(self) -> None:
        turn = 0

        def multi_turn_analyzer(user_input: str, history: str) -> dict[str, Any]:
            nonlocal turn
            turn += 1
            if turn == 1:
                return {
                    "response": "What programming language should we use?",
                    "needs_clarification": True,
                    "has_checklist": False,
                }
            return {
                "response": f"Got it ({user_input}). Checklist: 1. Python setup. Proceed?",
                "needs_clarification": False,
                "has_checklist": True,
            }

        shell = SessionShell(
            analyzer_fn=multi_turn_analyzer,
            planner_fn=lambda s: ["Setup project"],
        )

        thread_id = "test-multi-turn"
        # Turn 1: asks clarification
        shell.run("Build backend", thread_id)
        snap1 = shell.get_state(thread_id)
        assert snap1.tasks[0].interrupts[0].value["type"] == "clarification"

        # Turn 2: answers clarification, analyst produces checklist
        shell.resume(thread_id, "Python 3.12")
        snap2 = shell.get_state(thread_id)
        assert snap2.tasks[0].interrupts[0].value["type"] == "checklist_approval"

        # Turn 3: confirms checklist, completes plan
        res3 = shell.resume(thread_id, "confirm")
        assert res3["status"] == SessionStatus.COMPLETED
        assert res3["success_count"] == 1

    def test_session_routing_planner_failure(self) -> None:
        def mock_analyzer(user_input: str, history: str) -> dict[str, Any]:
            return {
                "response": "Checklist: 1. Setup. Proceed?",
                "needs_clarification": False,
                "has_checklist": True,
            }

        def failing_planner(spec: str) -> list[str]:
            return []  # Planner returns empty plan

        shell = SessionShell(
            analyzer_fn=mock_analyzer,
            planner_fn=failing_planner,
        )

        thread_id = "test-plan-fail"
        shell.run("Build app", thread_id)
        res = shell.resume(thread_id, "/yes")
        assert res["status"] == SessionStatus.PLAN_FAILED

    def test_session_item_failure_marks_overall_session_failed(self) -> None:
        def mock_analyzer(user_input: str, history: str) -> dict[str, Any]:
            return {
                "response": "Checklist: 1. Step 1. Proceed?",
                "needs_clarification": False,
                "has_checklist": True,
            }

        def failing_item_runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            return PlanItemResult(
                index=idx,
                sub_requirement=sub_req,
                status="failed",
                terminal_reason="error",
                red_confirmed=False,
                green_passed=False,
            )

        shell = SessionShell(
            analyzer_fn=mock_analyzer,
            planner_fn=lambda s: ["Flaky item"],
            loop_runner=failing_item_runner,
        )

        thread_id = "test-item-fail"
        shell.run("Run flaky test", thread_id)
        res = shell.resume(thread_id, "/yes")
        assert res["status"] == SessionStatus.FAILED
        assert res["failure_count"] == 1
        assert res["success_count"] == 0


# ── Mutation defense tests (Phase K) ─────────────────────────────────────────

import inspect
from app.session.shell import create_initial_session_state, SessionShell


class TestMutationDefensePhaseK:
    """Kill surviving mutants from Phase K mutation testing."""

    def test_create_initial_session_state_default_specification_is_empty(self) -> None:
        """Kill mutant 1: specification default '' -> 'XXXX'."""
        sig = inspect.signature(create_initial_session_state)
        assert sig.parameters["specification"].default == ""

    def test_run_default_specification_is_empty(self) -> None:
        """Kill run mutant 1: specification default '' -> 'XXXX'."""
        sig = inspect.signature(SessionShell.run)
        assert sig.parameters["specification"].default == ""

    def test_arun_default_specification_is_empty(self) -> None:
        """Kill arun mutant 1: specification default '' -> 'XXXX'."""
        sig = inspect.signature(SessionShell.arun)
        assert sig.parameters["specification"].default == ""

    def test_run_forwards_specification_to_create_state(self) -> None:
        """Kill run mutants 7, 10: specification forwarded as None or dropped."""
        from unittest.mock import MagicMock, patch

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {"status": "completed"}

        shell = SessionShell.__new__(SessionShell)
        shell._graph = mock_graph
        shell._checkpointer = MagicMock()

        with patch("app.session.shell.create_initial_session_state", wraps=create_initial_session_state) as mock_create:
            shell.run("hello", "thread-1", specification="my spec")
            mock_create.assert_called_once_with("thread-1", "hello", "my spec")

    @pytest.mark.asyncio
    async def test_arun_forwards_specification_to_create_state(self) -> None:
        """Kill arun mutants 7, 10: specification forwarded as None or dropped."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"status": "completed"})

        shell = SessionShell.__new__(SessionShell)
        shell._graph = mock_graph
        shell._checkpointer = MagicMock()

        with patch("app.session.shell.create_initial_session_state", wraps=create_initial_session_state) as mock_create:
            await shell.arun("hello", "thread-2", specification="async spec")
            mock_create.assert_called_once_with("thread-2", "hello", "async spec")

