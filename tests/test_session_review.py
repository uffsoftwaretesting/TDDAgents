"""
The user's review of the specification and of the plan (`app/session/review.py`, 2026-10-29):
upstream's plan approval (`ExitPlanModeV2Tool`) at the end of the engineer and of the planner.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.session.review import (
    EDIT_COMMAND,
    EDITED,
    PLAN_COMMAND,
    node_engineer_review,
    node_planner_review,
    plan_payload,
    specification_payload,
)
from app.session.shell import SessionShell
from app.session.state import SessionState, SessionStatus


def _engineer_review(answer: Any, state: SessionState) -> dict[str, Any]:
    with patch("app.session.review.interrupt", return_value=answer) as ask:
        out = node_engineer_review(state)
    assert ask.call_args.args[0] == specification_payload(state)
    return out


def _planner_review(answer: Any, state: SessionState) -> dict[str, Any]:
    with patch("app.session.review.interrupt", return_value=answer) as ask:
        out = node_planner_review(state)
    assert ask.call_args.args[0] == plan_payload(state)
    return out


SPEC_STATE: SessionState = {"specification": "# Spec", "user_prompts": ["req"], "audit_log": ["[Init]"]}
PLAN_STATE: SessionState = {"plan": ["Setup", "Core"], "user_prompts": ["req"], "audit_log": ["[Init]"]}


def test_the_constants():
    assert (EDITED, EDIT_COMMAND, PLAN_COMMAND) == ("edited", "/edit", "/plan")


def test_the_payloads():
    assert specification_payload(SPEC_STATE) == {"type": "specification_approval", "prompt": "# Spec"}
    assert specification_payload({}) == {"type": "specification_approval", "prompt": ""}
    assert plan_payload({**PLAN_STATE, "review_error": "Usage: /plan add <text>"}) == {
        "type": "plan_approval", "prompt": "▶ 1. Setup\n· 2. Core", "plan": ["Setup", "Core"],
        "error": "Usage: /plan add <text>"}
    assert plan_payload({}) == {"type": "plan_approval", "prompt": "(no sub-requirements)", "plan": [], "error": ""}


# ── the specification ───────────────────────────────────────────────────────


@pytest.mark.parametrize("reply", ["/yes", " /YES "])
def test_slash_yes_approves_the_specification(reply: str):
    out = _engineer_review(reply, SPEC_STATE)
    assert out == {"user_prompts": ["req", "/yes" if reply == "/yes" else "/YES"], "status": SessionStatus.PLANNING,
                   "audit_log": ["[Init]", "[Engineer] Specification approved."]}


def test_other_text_is_feedback_for_the_engineer():
    out = _engineer_review(" add a CLI ", SPEC_STATE)
    assert out == {"user_prompts": ["req", "add a CLI"], "engineer_feedback": "add a CLI",
                   "status": SessionStatus.SPECIFYING,
                   "audit_log": ["[Init]", "[Engineer] The user asked for changes."]}


def test_an_edit_replaces_the_specification_and_is_reviewed_again():
    out = _engineer_review({EDITED: "  # Edited\n"}, SPEC_STATE)
    assert out == {"specification": "# Edited", "user_prompts": ["req", "/edit"], "status": SessionStatus.SPECIFIED,
                   "audit_log": ["[Init]", "[Engineer] Specification edited by the user."]}


def test_a_headless_session_approves_the_specification():
    with patch("app.session.review.interrupt") as ask:
        out = node_engineer_review(SPEC_STATE, headless=True)
    ask.assert_not_called()
    assert out["status"] == SessionStatus.PLANNING and out["user_prompts"] == ["req", "/yes"]


# ── the plan ────────────────────────────────────────────────────────────────


def test_slash_yes_approves_the_plan():
    out = _planner_review("/yes", {**PLAN_STATE, "review_error": "old"})
    assert out == {"user_prompts": ["req", "/yes"], "review_error": "", "status": SessionStatus.PLANNED,
                   "audit_log": ["[Init]", "[Planner] Plan approved."]}


@pytest.mark.parametrize(("command", "plan"), [
    ("/plan add Docs", ["Setup", "Core", "Docs"]),
    ("/plan edit 2 Core logic", ["Setup", "Core logic"]),
    ("/plan remove 1", ["Core"]),
    ("/plan move 2 1", ["Core", "Setup"]),
])
def test_a_plan_command_changes_the_proposed_plan(command: str, plan: list[str]):
    out = _planner_review(command, {**PLAN_STATE, "review_error": "old"})
    assert out == {"plan": plan, "user_prompts": ["req", command], "review_error": "",
                   "status": SessionStatus.PLAN_PROPOSED}


@pytest.mark.parametrize(("command", "error"), [
    ("/plan add", "Usage: /plan add <text>"),
    ("/plan edit 2", "Usage: /plan edit <id> <text>"),
])
def test_a_plan_command_that_cannot_apply_is_shown_with_the_plan(command: str, error: str):
    out = _planner_review(command, PLAN_STATE)
    assert out == {"user_prompts": ["req", command], "review_error": error, "status": SessionStatus.PLAN_PROPOSED}


def test_an_edit_replaces_the_plan_one_item_per_line():
    out = _planner_review({EDITED: "Setup\n\n  Core  \nDocs\n"}, PLAN_STATE)
    assert out == {"plan": ["Setup", "Core", "Docs"], "review_error": "", "user_prompts": ["req", "/edit"],
                   "status": SessionStatus.PLAN_PROPOSED, "audit_log": ["[Init]", "[Planner] Plan edited by the user."]}


def test_other_text_is_feedback_for_the_planner():
    out = _planner_review("planning is missing Docker", PLAN_STATE)
    assert out == {"user_prompts": ["req", "planning is missing Docker"],
                   "planner_feedback": "planning is missing Docker", "review_error": "",
                   "status": SessionStatus.PLANNING, "audit_log": ["[Init]", "[Planner] The user asked for changes."]}


def test_a_headless_session_approves_the_plan():
    with patch("app.session.review.interrupt") as ask:
        out = node_planner_review(PLAN_STATE, headless=True)
    ask.assert_not_called()
    assert out["status"] == SessionStatus.PLANNED


def test_an_answer_that_is_neither_text_nor_an_edit_is_feedback():
    out = _planner_review({"other": "x"}, PLAN_STATE)
    assert out["planner_feedback"] == "{'other': 'x'}"


# ── through the graph ───────────────────────────────────────────────────────


def _shell() -> tuple[SessionShell, list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    engineer_calls: list[tuple[Any, ...]] = []
    planner_calls: list[tuple[Any, ...]] = []

    def engineer(*args: Any) -> str:
        engineer_calls.append(args)
        return "# Spec v2" if args[3] else "# Spec v1"

    def planner(*args: Any) -> list[str]:
        planner_calls.append(args)
        return ["Setup", "Core", "Docker"] if args[3] else ["Setup", "Core"]

    def analyst(user_input: str, history: str) -> dict[str, Any]:
        return {"response": "All clear.", "needs_clarification": False, "has_checklist": False}

    shell = SessionShell(MemorySaver(), analyzer_fn=analyst, engineer_fn=engineer, planner_fn=planner)
    return shell, engineer_calls, planner_calls


def test_the_reviews_loop_until_approved():
    shell, engineer_calls, planner_calls = _shell()
    shell.run("req", "t")
    (review,) = shell.pending_interrupts("t")
    assert review == {"type": "specification_approval", "prompt": "# Spec v1"}

    shell.resume("t", "add a CLI")
    assert engineer_calls[-1][2:] == ("# Spec v1", "add a CLI")
    assert shell.pending_interrupts("t")[0]["prompt"] == "# Spec v2"
    shell.resume("t", {EDITED: "# Spec v3"})
    assert shell.pending_interrupts("t")[0] == {"type": "specification_approval", "prompt": "# Spec v3"}
    shell.resume("t", "/yes")

    plan_review = shell.pending_interrupts("t")[0]
    assert (plan_review["type"], plan_review["plan"]) == ("plan_approval", ["Setup", "Core"])
    assert planner_calls == [("# Spec v3", [], [], "")]
    shell.resume("t", "add the Docker files")
    assert planner_calls[-1] == ("# Spec v3", [], ["Setup", "Core"], "add the Docker files")
    shell.resume("t", "/plan remove 2")
    assert shell.pending_interrupts("t")[0]["plan"] == ["Setup", "Docker"]
    shell.resume("t", "/plan add")
    assert shell.pending_interrupts("t")[0]["error"] == "Usage: /plan add <text>"
    result = shell.resume("t", "/yes")

    assert result["status"] == SessionStatus.PLANNED
    assert (result["specification"], result["plan"]) == ("# Spec v3", ["Setup", "Docker"])
    assert result["user_prompts"] == ["req", "add a CLI", "/edit", "/yes", "add the Docker files",
                                      "/plan remove 2", "/plan add", "/yes"]
    assert shell.is_finished("t")
