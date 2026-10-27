"""
The session shell (Part K, Phase 9): checkpointing, the analyst's `interrupt()`, the
engineer, the planner, and plan-item iteration.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END

from app.query.transitions import Terminal
from app.session.analyst import (
    APPROVAL,
    HEADLESS_CLARIFICATION_ERROR,
    node_analyst,
    node_analyst_review,
    review_payload,
)
from app.session.checkpointer import build_checkpointer, is_persistent_checkpointer
from app.session.engineer import node_engineer
from app.session.iteration import (
    node_evaluator,
    node_execute_plan_item,
    node_plan,
)
from app.session.shell import (
    RECURSION_LIMIT,
    SessionShell,
    build_session_run_config,
    create_initial_session_state,
    create_session_graph,
    route_after_analyst,
    route_after_engineer,
    route_after_evaluator,
    route_after_planner,
    route_after_review,
)
from app.session.state import PlanItemResult, SessionState, SessionStatus
from tests.retry_doubles import RetryableError


def ok_runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
    """A runner that reports an observed Red-then-Green cycle (the shell never invents one)."""
    return PlanItemResult(index=idx, sub_requirement=sub_req, status="success", terminal_reason="completed",
                          red_confirmed=True, green_passed=True)


def checklist(user_input: str, history: str) -> dict[str, Any]:
    return {"response": "Checklist: 1. Setup. Can I proceed?", "needs_clarification": False, "has_checklist": True}


def question(user_input: str, history: str) -> dict[str, Any]:
    return {"response": "Which currency?", "needs_clarification": True, "has_checklist": False}


def direct(user_input: str, history: str) -> dict[str, Any]:
    return {"response": "All clear.", "needs_clarification": False, "has_checklist": False}


def engineer(requirements: str, history: str) -> str:
    return f"SPEC({history})"


def planner(spec: str) -> list[str]:
    return ["Item A", "Item B"]


def make_shell(analyzer: Any = checklist, **kwargs: Any) -> SessionShell:
    kwargs.setdefault("checkpointer", MemorySaver())
    kwargs.setdefault("engineer_fn", engineer)
    kwargs.setdefault("planner_fn", planner)
    kwargs.setdefault("loop_runner", ok_runner)
    return SessionShell(analyzer_fn=analyzer, **kwargs)


# ==============================================================================
# Part K1: Checkpointer
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

    def test_is_persistent_checkpointer(self) -> None:
        assert is_persistent_checkpointer(MemorySaver()) is False
        assert is_persistent_checkpointer(MagicMock()) is True


# ==============================================================================
# Part K2: the analyst and its review
# ==============================================================================


class TestAnalystNode:
    def test_first_turn_records_the_conversation(self) -> None:
        out = node_analyst({"user_input": "  Build a calculator  "}, analyzer_fn=question)
        assert out["conversation_history"] == "[User]: Build a calculator\n[Analyst]: Which currency?"
        assert out["requirements"] == out["conversation_history"]
        assert out["current_response"] == "Which currency?"
        assert out["needs_clarification"] is True
        assert out["has_checklist"] is False
        assert out["interaction_count"] == 1
        assert out["user_confirmed"] is False
        assert out["status"] == SessionStatus.AWAITING_INPUT

    def test_later_turns_append_to_the_history(self) -> None:
        seen: list[tuple[str, str]] = []

        def analyzer(user_input: str, history: str) -> dict[str, Any]:
            seen.append((user_input, history))
            return checklist(user_input, history)

        state: SessionState = {
            "user_input": "EUR", "conversation_history": "[User]: a\n[Analyst]: b", "interaction_count": 3,
        }
        out = node_analyst(state, analyzer_fn=analyzer)
        assert seen == [("EUR", "[User]: a\n[Analyst]: b")]
        assert out["conversation_history"] == (
            "[User]: a\n[Analyst]: b\n\n[User]: EUR\n[Analyst]: Checklist: 1. Setup. Can I proceed?"
        )
        assert out["interaction_count"] == 4
        assert out["has_checklist"] is True
        assert out["status"] == SessionStatus.AWAITING_INPUT

    def test_no_round_cap(self) -> None:
        """The old fifth-round forced checklist is gone: the analyst asks until approved."""
        out = node_analyst({"user_input": "x", "interaction_count": 40}, analyzer_fn=question)
        assert out["has_checklist"] is False
        assert out["needs_clarification"] is True
        assert out["current_response"] == "Which currency?"
        assert out["interaction_count"] == 41

    def test_direct_acceptance_completes_the_analysis(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            out = node_analyst({"user_input": "Sum two ints"}, analyzer_fn=direct)
        assert out["status"] == SessionStatus.ANALYSIS_COMPLETE
        assert out["user_confirmed"] is True
        assert "Analyst accepted requirements directly without clarification." in caplog.text

    def test_missing_fields_default(self) -> None:
        out = node_analyst({}, analyzer_fn=lambda u, h: {})
        assert out["conversation_history"] == "[User]: \n[Analyst]: "
        assert out["status"] == SessionStatus.ANALYSIS_COMPLETE

    def test_error_fails_the_session(self, caplog: pytest.LogCaptureFixture) -> None:
        def broken(user_input: str, history: str) -> dict[str, Any]:
            raise ValueError("model down")

        with caplog.at_level(logging.ERROR):
            out = node_analyst({"user_input": "x", "audit_log": ["[Init]"]}, analyzer_fn=broken)
        assert out == {"status": SessionStatus.FAILED, "audit_log": ["[Init]", "[Analyst] Error: model down"]}
        assert "Analyst execution error: model down" in caplog.text

    def test_a_retryable_error_is_retried(self, no_retry_sleep: list[float]) -> None:
        calls: list[str] = []

        def flaky(user_input: str, history: str) -> dict[str, Any]:
            calls.append(user_input)
            if len(calls) < 3:
                raise RetryableError("503")
            return checklist(user_input, history)

        out = node_analyst({"user_input": "x"}, analyzer_fn=flaky)
        assert out["has_checklist"] is True
        assert calls == ["x", "x", "x"]
        assert len(no_retry_sleep) == 2


class TestAnalystReview:
    def test_payload(self) -> None:
        state: SessionState = {"current_response": "Q?", "has_checklist": False, "needs_clarification": True,
                               "interaction_count": 2}
        assert review_payload(state) == {"type": "clarification", "prompt": "Q?", "has_checklist": False,
                                         "needs_clarification": True, "interaction_count": 2}
        assert review_payload({"has_checklist": True})["type"] == "checklist_approval"
        assert review_payload({}) == {"type": "clarification", "prompt": "", "has_checklist": False,
                                      "needs_clarification": False, "interaction_count": 0}

    def test_headless_approves_a_checklist(self) -> None:
        out = node_analyst_review({"has_checklist": True, "user_prompts": ["req"]}, headless=True)
        assert out == {"user_prompts": ["req", APPROVAL], "user_confirmed": True, "needs_clarification": False,
                       "status": SessionStatus.ANALYSIS_COMPLETE}

    def test_headless_fails_on_a_clarifying_question(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.ERROR):
            out = node_analyst_review({"has_checklist": False, "audit_log": ["[Init]"]}, headless=True)
        assert out == {"status": SessionStatus.FAILED,
                       "audit_log": ["[Init]", f"[Analyst] {HEADLESS_CLARIFICATION_ERROR}."]}
        assert HEADLESS_CLARIFICATION_ERROR in caplog.text

    @pytest.mark.parametrize("reply", ["/yes", "  /YES  ", "/Yes"])
    def test_slash_yes_approves_a_checklist(self, reply: str) -> None:
        with patch("app.session.analyst.interrupt", return_value=reply) as ask:
            out = node_analyst_review({"has_checklist": True, "current_response": "C"})
        assert ask.call_args.args[0]["type"] == "checklist_approval"
        assert out["status"] == SessionStatus.ANALYSIS_COMPLETE
        assert out["user_confirmed"] is True
        assert out["user_prompts"] == [reply.strip()]

    @pytest.mark.parametrize("reply", ["yes", "y", "ok", "proceed", "add validation X"])
    def test_anything_else_is_feedback(self, reply: str) -> None:
        with patch("app.session.analyst.interrupt", return_value=reply):
            out = node_analyst_review({"has_checklist": True, "user_prompts": ["req"]})
        assert out == {"user_prompts": ["req", reply], "user_input": reply, "user_confirmed": False,
                       "has_checklist": False, "status": SessionStatus.GATHERING_REQUIREMENTS}

    def test_slash_yes_to_a_question_is_feedback(self) -> None:
        with patch("app.session.analyst.interrupt", return_value="/yes"):
            out = node_analyst_review({"has_checklist": False})
        assert out["status"] == SessionStatus.GATHERING_REQUIREMENTS
        assert out["user_input"] == "/yes"

    def test_resume_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch("app.session.analyst.interrupt", return_value=" EUR "), caplog.at_level(logging.INFO):
            node_analyst_review({})
        assert "Resumed from interrupt with user feedback: 'EUR'" in caplog.text


class TestEngineerNode:
    def test_writes_the_specification(self) -> None:
        seen: list[tuple[str, str]] = []

        def eng(requirements: str, history: str) -> str:
            seen.append((requirements, history))
            return "  # Spec  "

        state: SessionState = {"requirements": "R", "conversation_history": "H", "audit_log": ["[Init]"]}
        out = node_engineer(state, engineer_fn=eng)
        assert seen == [("R", "H")]
        assert out == {"specification": "# Spec", "status": SessionStatus.PLANNING,
                       "audit_log": ["[Init]", "[Engineer] Specification ready (6 characters)."]}

    def test_empty_specification_fails(self) -> None:
        out = node_engineer({}, engineer_fn=lambda r, h: "   ")
        assert out == {"status": SessionStatus.FAILED, "audit_log": ["[Engineer] Empty specification."]}

    def test_error_fails(self, caplog: pytest.LogCaptureFixture) -> None:
        def broken(r: str, h: str) -> str:
            raise RuntimeError("boom")

        with caplog.at_level(logging.ERROR):
            out = node_engineer({"audit_log": ["[Init]"]}, engineer_fn=broken)
        assert out == {"status": SessionStatus.FAILED, "audit_log": ["[Init]", "[Engineer] Error: boom"]}
        assert "Engineer execution error: boom" in caplog.text

    def test_logs_the_size(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            node_engineer({}, engineer_fn=lambda r, h: "abc")
        assert "Specification generated: 3 characters." in caplog.text

    def test_default_seam_is_the_engineer_agent(self) -> None:
        with patch("app.session.agents.engineer.generate_specification", return_value="S") as seam:
            out = node_engineer({"requirements": "R", "conversation_history": "H"})
        seam.assert_called_once_with("R", "H")
        assert out["specification"] == "S"


# ==============================================================================
# Part K3: planner, executor, evaluator
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

    def test_planner_plans_from_the_specification_only(self) -> None:
        seen: list[str] = []

        def capturing_planner(spec: str) -> list[str]:
            seen.append(spec)
            return ["Item 1"]

        state: SessionState = {"specification": "", "requirements": "Build auth", "user_input": "x"}
        out = node_plan(state, planner_fn=capturing_planner)
        assert out["status"] == SessionStatus.PLAN_FAILED
        assert seen == []
        node_plan({"specification": "  The spec  ", "requirements": "ignored"}, planner_fn=capturing_planner)
        assert seen == ["The spec"]

    def test_planner_appends_to_the_audit_log(self) -> None:
        out = node_plan({"specification": "S", "audit_log": ["[Init]"]}, planner_fn=lambda s: ["A"])
        assert out["audit_log"] == ["[Init]", "[Planner] Generated plan with 1 items:\n1. A"]
        failed = node_plan({"specification": "S", "audit_log": ["[Init]"]}, planner_fn=lambda s: [])
        assert failed["audit_log"] == ["[Init]", "[Planner] Planner returned an empty plan."]

    def test_planner_retries_a_retryable_error(self, no_retry_sleep: list[float]) -> None:
        calls: list[str] = []

        def flaky(spec: str) -> list[str]:
            calls.append(spec)
            if len(calls) == 1:
                raise RetryableError("overloaded")
            return ["A"]

        out = node_plan({"specification": "S"}, planner_fn=flaky)
        assert out["plan"] == ["A"]
        assert calls == ["S", "S"]
        assert len(no_retry_sleep) == 1

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

    def test_executor_success_preserves_history(self) -> None:
        state: SessionState = {
            "plan": ["Task 1"],
            "plan_index": 0,
            "success_count": 2,
            "failure_count": 1,
            "audit_log": ["[Init] Started"],
            "subreq_results": [{"prev": 1}],
        }
        out = node_execute_plan_item(state, loop_runner=ok_runner)
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

    def test_executor_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            node_execute_plan_item({"plan": ["Subtask 1"], "plan_index": 0}, loop_runner=ok_runner)
        assert "Executing plan item 1/1: 'Subtask 1'" in caplog.text

    def test_executor_without_runner_refuses_to_fabricate(self) -> None:
        """Without a runner nothing observed Red or Green, so nothing may be reported."""
        from app.session.iteration import LoopRunnerNotConfigured

        state: SessionState = {"plan": ["Subtask 1"], "plan_index": 0, "success_count": 0}
        with pytest.raises(LoopRunnerNotConfigured, match="refusing to fabricate"):
            node_execute_plan_item(state)
        assert issubclass(LoopRunnerNotConfigured, RuntimeError)

    def test_executor_without_runner_is_fine_once_the_plan_is_done(self) -> None:
        assert node_execute_plan_item({"plan": ["A"], "plan_index": 1})["status"] == SessionStatus.COMPLETED
        assert node_execute_plan_item({"plan": [], "plan_index": 0})["status"] == SessionStatus.COMPLETED

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
# Routing and the compiled graph
# ==============================================================================


class TestRoutersAndState:
    def test_initial_state(self) -> None:
        state = create_initial_session_state("t-1", "Build X")
        assert state == {
            "session_id": "t-1", "user_input": "Build X", "user_prompts": ["Build X"], "specification": "",
            "requirements": "", "conversation_history": "", "needs_clarification": False, "has_checklist": False,
            "user_confirmed": False, "interaction_count": 0, "plan": [], "plan_index": 0, "subreq_results": [],
            "status": SessionStatus.INITIALIZING, "audit_log": ["[Session] Started thread 't-1'."],
            "success_count": 0, "failure_count": 0,
        }

    def test_run_config_has_no_ceiling(self) -> None:
        assert RECURSION_LIMIT == sys.maxsize
        assert build_session_run_config("t") == {"configurable": {"thread_id": "t"}, "recursion_limit": sys.maxsize}

    def test_route_after_analyst(self) -> None:
        assert route_after_analyst({"status": SessionStatus.ANALYSIS_COMPLETE}) == "engineer"
        assert route_after_analyst({"status": SessionStatus.AWAITING_INPUT}) == "analyst_review"
        assert route_after_analyst({"status": SessionStatus.FAILED}) == END
        assert route_after_analyst({}) == END

    def test_route_after_review(self) -> None:
        assert route_after_review({"status": SessionStatus.ANALYSIS_COMPLETE}) == "engineer"
        assert route_after_review({"status": SessionStatus.GATHERING_REQUIREMENTS}) == "analyst"
        assert route_after_review({"status": SessionStatus.FAILED}) == END

    def test_route_after_engineer(self) -> None:
        assert route_after_engineer({"status": SessionStatus.PLANNING}) == "planner"
        assert route_after_engineer({"status": SessionStatus.FAILED}) == END

    def test_route_after_planner(self) -> None:
        assert route_after_planner({"status": SessionStatus.EXECUTING_PLAN_ITEM, "plan": ["A"]}) == "execute_plan_item"
        assert route_after_planner({"status": SessionStatus.PLAN_FAILED, "plan": ["A"]}) == END
        assert route_after_planner({"status": SessionStatus.EXECUTING_PLAN_ITEM, "plan": []}) == END
        assert route_after_planner({"status": SessionStatus.EXECUTING_PLAN_ITEM}) == END

    def test_route_after_evaluator(self) -> None:
        assert route_after_evaluator({"status": SessionStatus.EXECUTING_PLAN_ITEM}) == "execute_plan_item"
        assert route_after_evaluator({"status": SessionStatus.COMPLETED}) == END

    def test_default_checkpointer_is_memory(self) -> None:
        shell = SessionShell()
        assert isinstance(shell.checkpointer, MemorySaver)
        assert create_session_graph() is not None

    def test_graph_nodes(self) -> None:
        nodes = set(make_shell().graph.get_graph().nodes)
        assert {"analyst", "analyst_review", "engineer", "planner", "execute_plan_item", "evaluator"} <= nodes


class TestFullSessionFlow:
    def test_interactive_happy_path(self) -> None:
        shell = make_shell()
        shell.run("Build a payment gateway", "t")
        assert shell.has_session("t")
        assert not shell.is_finished("t")
        (payload,) = shell.pending_interrupts("t")
        assert payload["type"] == "checklist_approval"
        assert payload["prompt"] == "Checklist: 1. Setup. Can I proceed?"
        result = shell.resume("t", "/yes")
        assert result["status"] == SessionStatus.COMPLETED
        assert result["specification"] == (
            "SPEC([User]: Build a payment gateway\n[Analyst]: Checklist: 1. Setup. Can I proceed?)"
        )
        assert result["plan"] == ["Item A", "Item B"]
        assert result["success_count"] == 2
        assert result["user_prompts"] == ["Build a payment gateway", "/yes"]
        assert shell.is_finished("t")
        assert shell.pending_interrupts("t") == []

    def test_the_analyst_is_not_asked_again_on_resume(self) -> None:
        """The interrupt is in its own node, so resuming never repeats the model call."""
        calls: list[str] = []

        def analyzer(user_input: str, history: str) -> dict[str, Any]:
            calls.append(user_input)
            return checklist(user_input, history)

        shell = make_shell(analyzer)
        shell.run("req", "t")
        shell.resume("t", "/yes")
        assert calls == ["req"]

    def test_feedback_cycles_back_to_the_analyst(self) -> None:
        answers = iter([question, checklist])

        def analyzer(user_input: str, history: str) -> dict[str, Any]:
            return next(answers)(user_input, history)

        shell = make_shell(analyzer)
        shell.run("req", "t")
        assert shell.pending_interrupts("t")[0]["type"] == "clarification"
        shell.resume("t", "EUR")
        assert shell.pending_interrupts("t")[0]["type"] == "checklist_approval"
        result = shell.resume("t", "/yes")
        assert result["status"] == SessionStatus.COMPLETED
        assert result["conversation_history"].count("[User]:") == 2
        assert "[User]: EUR" in result["conversation_history"]
        assert result["user_prompts"] == ["req", "EUR", "/yes"]

    def test_headless_runs_without_interrupts(self) -> None:
        shell = make_shell(headless=True)
        result = shell.run("req", "t")
        assert result["status"] == SessionStatus.COMPLETED
        assert shell.pending_interrupts("t") == []
        assert result["user_prompts"] == ["req", "/yes"]

    def test_headless_fails_on_a_question(self) -> None:
        shell = make_shell(question, headless=True)
        result = shell.run("req", "t")
        assert result["status"] == SessionStatus.FAILED
        assert result["plan"] == []

    def test_engineer_failure_ends_the_session(self) -> None:
        shell = make_shell(direct, engineer_fn=lambda r, h: "")
        result = shell.run("req", "t")
        assert result["status"] == SessionStatus.FAILED
        assert result["plan"] == []

    def test_planner_failure_ends_the_session(self) -> None:
        shell = make_shell(direct, planner_fn=lambda s: [])
        assert shell.run("req", "t")["status"] == SessionStatus.PLAN_FAILED

    def test_item_failure_marks_the_session_failed(self) -> None:
        def runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            return PlanItemResult(index=idx, sub_requirement=sub_req, status="failed",
                                  terminal_reason=Terminal.MODEL_ERROR.value, red_confirmed=False, green_passed=False)

        result = make_shell(direct, loop_runner=runner).run("req", "t")
        assert result["status"] == SessionStatus.FAILED
        assert result["failure_count"] == 2

    def test_a_restarted_process_continues_from_the_checkpoint(self) -> None:
        shared = MemorySaver()
        make_shell(checkpointer=shared).run("req", "t")
        shell_b = make_shell(checkpointer=shared)
        assert shell_b.pending_interrupts("t")[0]["type"] == "checklist_approval"
        assert shell_b.get_state("t").values["user_confirmed"] is False
        assert shell_b.resume("t", "/yes")["status"] == SessionStatus.COMPLETED

    def test_continue_session_reruns_an_unfinished_item(self) -> None:
        """A plan item whose process died is run again when the thread continues."""
        shared = MemorySaver()
        calls: list[int] = []

        def dying(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            calls.append(idx)
            if idx == 1:
                raise KeyboardInterrupt
            return ok_runner(sub_req, idx, state)

        with pytest.raises(KeyboardInterrupt):
            make_shell(direct, checkpointer=shared, loop_runner=dying).run("req", "t")
        shell_b = make_shell(direct, checkpointer=shared)
        assert shell_b.has_session("t") and not shell_b.is_finished("t")
        result = shell_b.continue_session("t")
        assert calls == [0, 1]
        assert result["status"] == SessionStatus.COMPLETED
        assert [r["index"] for r in result["subreq_results"]] == [0, 1]

    def test_an_interactive_pause_continued_headless_is_decided_by_the_shell(self) -> None:
        shared = MemorySaver()
        make_shell(checkpointer=shared).run("req", "t")
        result = make_shell(checkpointer=shared, headless=True).continue_session("t")
        assert result["status"] == SessionStatus.COMPLETED

    def test_unknown_thread(self) -> None:
        shell = make_shell()
        assert shell.has_session("nope") is False
        assert shell.is_finished("nope") is False
        assert shell.pending_interrupts("nope") == []


class TestMutationDefensePhase9:
    """Gaps the Phase 9 mutation run surfaced."""

    def test_default_seams_are_the_agents(self) -> None:
        with patch("app.session.agents.analyst.analyze_requirements", return_value=direct("", "")) as seam:
            out = node_analyst({"user_input": "u", "conversation_history": "h"})
        seam.assert_called_once_with("u", "h")
        assert out["status"] == SessionStatus.ANALYSIS_COMPLETE
        with patch("app.session.agents.planner.generate_plan", return_value=["A"]) as plan_seam:
            assert node_plan({"specification": "S"})["plan"] == ["A"]
        plan_seam.assert_called_once_with("S")

    def test_the_runner_receives_the_item_and_the_state(self) -> None:
        seen: list[Any] = []

        def runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            seen.append((sub_req, idx, state))
            return ok_runner(sub_req, idx, state)

        state: SessionState = {"plan": ["A", "B"], "plan_index": 1, "session_id": "t"}
        node_execute_plan_item(state, loop_runner=runner)
        assert seen == [("B", 1, state)]

    def test_a_sync_runner_may_return_an_awaitable(self) -> None:
        async def result(sub_req: str, idx: int) -> PlanItemResult:
            return ok_runner(sub_req, idx, {})

        out = node_execute_plan_item({"plan": ["A"], "plan_index": 0}, loop_runner=lambda r, i, s: result(r, i))
        assert out["status"] == SessionStatus.ITEM_COMPLETE

    def test_a_crashed_item_is_recorded_whole(self) -> None:
        def broken(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
            raise RuntimeError("x")

        out = node_execute_plan_item({"plan": ["A", "B"], "plan_index": 1}, loop_runner=broken)
        assert out["subreq_results"] == [{
            "index": 1, "sub_requirement": "B", "status": "failed", "terminal_reason": "error",
            "red_confirmed": False, "green_passed": False, "iterations": 1, "error_message": "x", "audit_notes": "",
        }]

    def test_wrong_type_names_the_type(self) -> None:
        out = node_execute_plan_item({"plan": ["A"], "plan_index": 0}, loop_runner=lambda r, i, s: 3)
        assert out["subreq_results"][0]["error_message"] == "loop_runner must return PlanItemResult, got <class 'int'>"

    def test_evaluator_counts_default_to_zero(self) -> None:
        out = node_evaluator({"plan": ["A"], "plan_index": 0})
        assert out["audit_log"] == ["[Evaluator] Plan finished. Status: completed (Success=0, Failed=0)."]

    def test_run_records_the_thread_id(self) -> None:
        shell = make_shell(direct)
        assert shell.run("req", "t-7")["session_id"] == "t-7"

    def test_create_session_graph_builds_a_checkpointer_when_none_is_given(self) -> None:
        graph = create_session_graph(analyzer_fn=question)
        graph.invoke(create_initial_session_state("t", "req"), config=build_session_run_config("t"))
        assert graph.get_state(build_session_run_config("t")).values["session_id"] == "t"

    def test_shells_are_interactive_by_default(self) -> None:
        shell = make_shell(question)
        shell.run("req", "t")
        assert shell.pending_interrupts("t")[0]["type"] == "clarification"
        graph = create_session_graph(MemorySaver(), analyzer_fn=question)
        graph.invoke(create_initial_session_state("t", "req"), config=build_session_run_config("t"))
        assert graph.get_state(build_session_run_config("t")).tasks[0].interrupts

    def test_engineer_missing_fields_are_empty(self) -> None:
        seen: list[tuple[str, str]] = []

        def eng(r: str, h: str) -> str:
            seen.append((r, h))
            return "S"

        node_engineer({}, engineer_fn=eng)
        assert seen == [("", "")]

    def test_memory_uris_are_not_warned_about(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            for value in (":memory:", ":MEMORY:", "memory", " MEMORY ", ""):
                assert isinstance(build_checkpointer(value), MemorySaver)
        assert caplog.text == ""

    def test_postgres_saver_gets_the_connection(self) -> None:
        with patch("psycopg.connect") as connect, patch("langgraph.checkpoint.postgres.PostgresSaver") as saver_cls:
            build_checkpointer("postgresql://u:p@h/db")
        saver_cls.assert_called_once_with(connect.return_value)

    def test_a_saver_without_setup_is_used_as_is(self) -> None:
        class NoSetup:
            pass

        saver: Any = NoSetup()
        with patch("psycopg.connect"), patch("langgraph.checkpoint.postgres.PostgresSaver", return_value=saver):
            assert build_checkpointer("postgresql://h/db") is saver

    def test_a_uri_without_credentials_logs_the_whole_uri(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch("psycopg.connect"), patch("langgraph.checkpoint.postgres.PostgresSaver"), \
                caplog.at_level(logging.INFO):
            build_checkpointer("postgresql://localhost:5432/db")
        assert "Configured PostgresSaver checkpointer for URI: postgresql://localhost:5432/db" in caplog.text

    def test_headless_clarification_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.ERROR):
            node_analyst_review({}, headless=True)
        assert f"Headless session stopped: {HEADLESS_CLARIFICATION_ERROR}" in caplog.text
