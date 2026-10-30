"""
The session shell (Part K, Phases 9 and 11): checkpointing, the analyst's `interrupt()`, the
engineer, the planner, and the `/requirements` re-scope.
"""

from __future__ import annotations

import logging
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END

from app.session.analyst import (
    APPROVAL,
    HEADLESS_CLARIFICATION_ERROR,
    node_analyst,
    node_analyst_review,
    review_payload,
)
from app.session.checkpointer import build_checkpointer, is_persistent_checkpointer
from app.session.engineer import node_engineer
from app.session.planning import node_plan
from app.session.shell import (
    RECURSION_LIMIT,
    SessionShell,
    build_session_run_config,
    create_initial_session_state,
    create_session_graph,
    route_after_analyst,
    route_after_engineer,
    route_after_engineer_review,
    route_after_planner,
    route_after_planner_review,
    route_after_review,
)
from app.session.state import SessionState, SessionStatus
from tests.retry_doubles import RetryableError


def checklist(user_input: str, history: str) -> dict[str, Any]:
    return {"response": "Checklist: 1. Setup. Can I proceed?", "needs_clarification": False, "has_checklist": True}


def question(user_input: str, history: str) -> dict[str, Any]:
    return {"response": "Which currency?", "needs_clarification": True, "has_checklist": False}


def direct(user_input: str, history: str) -> dict[str, Any]:
    return {"response": "All clear.", "needs_clarification": False, "has_checklist": False}


def engineer(requirements: str, history: str, previous: str = "", feedback: str = "") -> str:
    return f"SPEC({history})" if not feedback else f"{previous}+{feedback}"


def planner(spec: str, completed: Any = (), plan: Any = (), feedback: str = "") -> list[str]:
    return ["Item A", "Item B"] if not feedback else [*plan, feedback]


def approve(shell: SessionShell, thread_id: str, *expected: str) -> dict[str, Any]:
    """Answer `/yes` to each pending review in turn (`expected` names their types)."""
    result: dict[str, Any] = {}
    for kind in expected:
        assert shell.pending_interrupts(thread_id)[0]["type"] == kind
        result = shell.resume(thread_id, "/yes")
    return result


#: The two reviews after the analyst's checklist (2026-10-29).
REVIEWS = ("specification_approval", "plan_approval")


def make_shell(analyzer: Any = checklist, **kwargs: Any) -> SessionShell:
    kwargs.setdefault("checkpointer", MemorySaver())
    kwargs.setdefault("engineer_fn", engineer)
    kwargs.setdefault("planner_fn", planner)
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
        seen: list[tuple[str, str, str, str]] = []

        def eng(requirements: str, history: str, previous: str, feedback: str) -> str:
            seen.append((requirements, history, previous, feedback))
            return "  # Spec  "

        state: SessionState = {"requirements": "R", "conversation_history": "H", "audit_log": ["[Init]"],
                               "specification": "# Old"}
        out = node_engineer(state, engineer_fn=eng)
        # Without feedback the previous specification is not passed on.
        assert seen == [("R", "H", "", "")]
        assert out == {"specification": "# Spec", "engineer_feedback": "", "status": SessionStatus.SPECIFIED,
                       "audit_log": ["[Init]", "[Engineer] Specification ready (6 characters)."]}

    def test_rewrites_the_specification_from_the_users_feedback(self) -> None:
        seen: list[tuple[str, ...]] = []

        def eng(*args: str) -> str:
            seen.append(args)
            return "# New"

        out = node_engineer({"requirements": "R", "conversation_history": "H", "specification": "# Old",
                             "engineer_feedback": "add a CLI"}, engineer_fn=eng)
        assert seen == [("R", "H", "# Old", "add a CLI")]
        assert (out["specification"], out["engineer_feedback"]) == ("# New", "")

    def test_empty_specification_fails(self) -> None:
        out = node_engineer({}, engineer_fn=lambda r, h, *_: "   ")
        assert out == {"status": SessionStatus.FAILED, "audit_log": ["[Engineer] Empty specification."]}

    def test_error_fails(self, caplog: pytest.LogCaptureFixture) -> None:
        def broken(r: str, h: str, *_: str) -> str:
            raise RuntimeError("boom")

        with caplog.at_level(logging.ERROR):
            out = node_engineer({"audit_log": ["[Init]"]}, engineer_fn=broken)
        assert out == {"status": SessionStatus.FAILED, "audit_log": ["[Init]", "[Engineer] Error: boom"]}
        assert "Engineer execution error: boom" in caplog.text

    def test_logs_the_size(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            node_engineer({}, engineer_fn=lambda r, h, *_: "abc")
        assert "Specification generated: 3 characters." in caplog.text

    def test_default_seam_is_the_engineer_agent(self) -> None:
        with patch("app.session.agents.engineer.generate_specification", return_value="S") as seam:
            out = node_engineer({"requirements": "R", "conversation_history": "H"})
        seam.assert_called_once_with("R", "H", "", "")
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

        def capturing_planner(spec: str, completed: Any, *_: Any) -> list[str]:
            seen.append(spec)
            return ["Item 1"]

        state: SessionState = {"specification": "", "requirements": "Build auth", "user_input": "x"}
        out = node_plan(state, planner_fn=capturing_planner)
        assert out["status"] == SessionStatus.PLAN_FAILED
        assert seen == []
        node_plan({"specification": "  The spec  ", "requirements": "ignored"}, planner_fn=capturing_planner)
        assert seen == ["The spec"]

    def test_planner_appends_to_the_audit_log(self) -> None:
        out = node_plan({"specification": "S", "audit_log": ["[Init]"]}, planner_fn=lambda s, c, *_: ["A"])
        assert out["audit_log"] == ["[Init]", "[Planner] Generated plan with 1 items:\n1. A"]
        failed = node_plan({"specification": "S", "audit_log": ["[Init]"]}, planner_fn=lambda s, c, *_: [])
        assert failed["audit_log"] == ["[Init]", "[Planner] Planner returned an empty plan."]

    def test_planner_retries_a_retryable_error(self, no_retry_sleep: list[float]) -> None:
        calls: list[str] = []

        def flaky(spec: str, completed: Any, *_: Any) -> list[str]:
            calls.append(spec)
            if len(calls) == 1:
                raise RetryableError("overloaded")
            return ["A"]

        out = node_plan({"specification": "S"}, planner_fn=flaky)
        assert out["plan"] == ["A"]
        assert calls == ["S", "S"]
        assert len(no_retry_sleep) == 1

    def test_planner_exception_fails(self) -> None:
        def crashing_planner(spec: str, completed: Any, *_: Any) -> list[str]:
            raise ValueError("Planner timeout")

        state: SessionState = {"specification": "Build app"}
        out = node_plan(state, planner_fn=crashing_planner)
        assert out["status"] == SessionStatus.PLAN_FAILED
        assert out["audit_log"] == ["Planner error: Planner timeout"]
        assert out["plan"] == []

    def test_planner_empty_list_fails(self) -> None:
        state: SessionState = {"specification": "Build app"}
        out = node_plan(state, planner_fn=lambda s, c, *_: [])
        assert out["status"] == SessionStatus.PLAN_FAILED
        assert out["audit_log"] == ["[Planner] Planner returned an empty plan."]
        assert out["plan"] == []

    def test_planner_successful_plan(self) -> None:
        items = ["Step 1: Core", "Step 2: CLI", "Step 3: Docs"]
        state: SessionState = {"specification": "Build complete app"}
        out = node_plan(state, planner_fn=lambda s, c, *_: items)
        assert out["status"] == SessionStatus.PLAN_PROPOSED
        assert out["plan"] == items
        assert set(out) == {"plan", "planner_feedback", "status", "audit_log"}
        expected_log = "[Planner] Generated plan with 3 items:\n1. Step 1: Core\n2. Step 2: CLI\n3. Step 3: Docs"
        assert out["audit_log"] == [expected_log]

    def test_the_planner_is_told_what_is_already_done(self) -> None:
        seen: list[Any] = []

        def capturing(spec: str, completed: Any, *_: Any) -> list[str]:
            seen.append(completed)
            return ["C"]

        node_plan({"specification": "S"}, planner_fn=capturing)
        node_plan({"specification": "S", "completed_items": ["A", "B"]}, planner_fn=capturing)
        assert seen == [[], ["A", "B"]]

    def test_a_rescope_with_nothing_left_is_planned_not_failed(self) -> None:
        out = node_plan({"specification": "S", "completed_items": ["A"]}, planner_fn=lambda s, c, *_: [])
        assert (out["status"], out["plan"]) == (SessionStatus.PLAN_PROPOSED, [])

    def test_replans_from_the_users_feedback(self) -> None:
        seen: list[tuple[Any, ...]] = []

        def capturing(*args: Any) -> list[str]:
            seen.append(args)
            return ["New"]

        state: SessionState = {"specification": "S", "plan": ["Old"], "completed_items": ["A"],
                               "planner_feedback": "split it"}
        out = node_plan(state, planner_fn=capturing)
        assert seen == [("S", ["A"], ["Old"], "split it")]
        assert (out["plan"], out["planner_feedback"]) == (["New"], "")
        node_plan({"specification": "S", "plan": ["Old"]}, planner_fn=capturing)
        assert seen[-1] == ("S", [], [], "")

    def test_planner_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            node_plan({"specification": "Build app"}, planner_fn=lambda s, c, *_: ["A", "B"])
        assert "Generated plan with 2 sub-requirements." in caplog.text

    def test_planner_error_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        def crashing(s: str, c: Any, *_: Any) -> list[str]:
            raise RuntimeError("Planner dead")

        with caplog.at_level(logging.ERROR):
            node_plan({"specification": "Build app"}, planner_fn=crashing)
        assert "Planner execution error: Planner dead" in caplog.text


# ==============================================================================
# Routing and the compiled graph
# ==============================================================================


class TestRoutersAndState:
    def test_initial_state(self) -> None:
        state = create_initial_session_state("t-1", "Build X")
        assert state == {
            "session_id": "t-1", "user_input": "Build X", "user_prompts": ["Build X"], "specification": "",
            "requirements": "", "conversation_history": "", "needs_clarification": False, "has_checklist": False,
            "user_confirmed": False, "interaction_count": 0, "plan": [], "completed_items": [],
            "status": SessionStatus.INITIALIZING, "audit_log": ["[Session] Started thread 't-1'."],
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
        assert route_after_engineer({"status": SessionStatus.SPECIFIED}) == "engineer_review"
        assert route_after_engineer({"status": SessionStatus.PLANNING}) == END
        assert route_after_engineer({"status": SessionStatus.FAILED}) == END

    def test_route_after_engineer_review(self) -> None:
        assert route_after_engineer_review({"status": SessionStatus.PLANNING}) == "planner"
        assert route_after_engineer_review({"status": SessionStatus.SPECIFYING}) == "engineer"
        assert route_after_engineer_review({"status": SessionStatus.SPECIFIED}) == "engineer_review"
        assert route_after_engineer_review({"status": SessionStatus.FAILED}) == END

    def test_route_after_planner(self) -> None:
        assert route_after_planner({"status": SessionStatus.PLAN_PROPOSED}) == "planner_review"
        assert route_after_planner({"status": SessionStatus.PLAN_FAILED}) == END

    def test_route_after_planner_review(self) -> None:
        assert route_after_planner_review({"status": SessionStatus.PLANNED}) == END
        assert route_after_planner_review({"status": SessionStatus.PLANNING}) == "planner"
        assert route_after_planner_review({"status": SessionStatus.PLAN_PROPOSED}) == "planner_review"

    def test_default_checkpointer_is_memory(self) -> None:
        shell = SessionShell()
        assert isinstance(shell.checkpointer, MemorySaver)
        assert create_session_graph() is not None

    def test_graph_nodes(self) -> None:
        nodes = set(make_shell().graph.get_graph().nodes)
        assert {"analyst", "analyst_review", "engineer", "engineer_review", "planner", "planner_review"} <= nodes
        assert not {"execute_plan_item", "evaluator"} & nodes


class TestFullSessionFlow:
    def test_interactive_happy_path(self) -> None:
        shell = make_shell()
        shell.run("Build a payment gateway", "t")
        assert shell.has_session("t")
        assert not shell.is_finished("t")
        (payload,) = shell.pending_interrupts("t")
        assert payload["type"] == "checklist_approval"
        assert payload["prompt"] == "Checklist: 1. Setup. Can I proceed?"
        result = approve(shell, "t", "checklist_approval", *REVIEWS)
        assert result["status"] == SessionStatus.PLANNED
        assert result["specification"] == (
            "SPEC([User]: Build a payment gateway\n[Analyst]: Checklist: 1. Setup. Can I proceed?)"
        )
        assert result["plan"] == ["Item A", "Item B"]
        assert result["user_prompts"] == ["Build a payment gateway", "/yes", "/yes", "/yes"]
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
        result = approve(shell, "t", "checklist_approval", *REVIEWS)
        assert result["status"] == SessionStatus.PLANNED
        assert result["conversation_history"].count("[User]:") == 2
        assert "[User]: EUR" in result["conversation_history"]
        assert result["user_prompts"] == ["req", "EUR", "/yes", "/yes", "/yes"]

    def test_headless_runs_without_interrupts(self) -> None:
        shell = make_shell(headless=True)
        result = shell.run("req", "t")
        assert result["status"] == SessionStatus.PLANNED
        assert shell.pending_interrupts("t") == []
        assert result["user_prompts"] == ["req", "/yes", "/yes", "/yes"]

    def test_headless_fails_on_a_question(self) -> None:
        shell = make_shell(question, headless=True)
        result = shell.run("req", "t")
        assert result["status"] == SessionStatus.FAILED
        assert result["plan"] == []

    def test_engineer_failure_ends_the_session(self) -> None:
        shell = make_shell(direct, engineer_fn=lambda r, h, *_: "")
        result = shell.run("req", "t")
        assert result["status"] == SessionStatus.FAILED
        assert result["plan"] == []

    def test_planner_failure_ends_the_session(self) -> None:
        shell = make_shell(direct, planner_fn=lambda s, c, *_: [])
        shell.run("req", "t")
        assert shell.resume("t", "/yes")["status"] == SessionStatus.PLAN_FAILED

    def test_a_restarted_process_continues_from_the_checkpoint(self) -> None:
        shared = MemorySaver()
        make_shell(checkpointer=shared).run("req", "t")
        shell_b = make_shell(checkpointer=shared)
        assert shell_b.pending_interrupts("t")[0]["type"] == "checklist_approval"
        assert shell_b.get_state("t").values["user_confirmed"] is False
        assert approve(shell_b, "t", "checklist_approval", *REVIEWS)["status"] == SessionStatus.PLANNED

    def test_an_interactive_pause_continued_headless_is_decided_by_the_shell(self) -> None:
        shared = MemorySaver()
        make_shell(checkpointer=shared).run("req", "t")
        result = make_shell(checkpointer=shared, headless=True).continue_session("t")
        assert result["status"] == SessionStatus.PLANNED

    def test_rescope_runs_the_analysis_again_and_passes_the_done_items(self) -> None:
        seen: list[Any] = []

        def capturing(spec: str, completed: Any, *_: Any) -> list[str]:
            seen.append(completed)
            return ["C"]

        inputs: list[str] = []

        def analyzer(user_input: str, history: str) -> dict[str, Any]:
            inputs.append(user_input)
            return direct(user_input, history)

        shell = make_shell(analyzer, planner_fn=capturing)
        shell.run("req", "t")
        approve(shell, "t", *REVIEWS)
        shell.rescope("t", "add CSV export", ["Item A"])
        result = approve(shell, "t", *REVIEWS)
        assert result["status"] == SessionStatus.PLANNED
        assert result["plan"] == ["C"]
        assert seen == [[], ["Item A"]]
        assert inputs == ["req", "add CSV export"]
        assert result["user_prompts"] == ["req", "/yes", "/yes", "add CSV export", "/yes", "/yes"]
        assert "[User]: add CSV export" in result["conversation_history"]
        assert result["completed_items"] == ["Item A"]

    def test_rescope_reaches_the_checklist_review_again(self) -> None:
        shell = make_shell()
        shell.run("req", "t")
        approve(shell, "t", "checklist_approval", *REVIEWS)
        shell.rescope("t", "more", [])
        assert approve(shell, "t", "checklist_approval", *REVIEWS)["status"] == SessionStatus.PLANNED

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
        plan_seam.assert_called_once_with("S", [], [], "")

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

        def eng(r: str, h: str, *_: str) -> str:
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
