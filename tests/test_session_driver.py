"""Phases 9 and 11: the entry point's session driver — the shell, a thread, then development."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.config.config import Config
from app.query.transitions import Terminal
from app.session import driver
from app.session.driver import (
    MEMORY_CHECKPOINT,
    SessionOptions,
    SessionRun,
    answer_interrupts,
    build_session_shell,
    checkpoint_uri,
    derive_thread_id,
    develop_headless,
    development_run_options,
    drive_session,
    fresh_thread_id,
    rescope_session,
)
from app.session.shell import SessionShell
from app.session.state import SessionStatus
from app.tdd.plan import ItemStatus


def checklist(user_input: str, history: str) -> dict[str, Any]:
    return {"response": "Checklist. Can I proceed?", "needs_clarification": False, "has_checklist": True}


def question(user_input: str, history: str) -> dict[str, Any]:
    return {"response": "Which?", "needs_clarification": True, "has_checklist": False}


def build(options: SessionOptions, analyzer: Any = checklist, planner: Any = None) -> tuple[SessionShell, Any]:
    workspace = object()
    shell, ws = build_session_shell(
        "t", options, workspace_factory=lambda thread_id: workspace, analyzer_fn=analyzer,
        engineer_fn=lambda r, h: "SPEC", planner_fn=planner or (lambda s, c: ["A"]),
    )
    assert ws is workspace
    return shell, ws


def test_thread_ids() -> None:
    assert derive_thread_id("Build X") == derive_thread_id("Build X")
    assert derive_thread_id("Build X") != derive_thread_id("Build Y")
    assert derive_thread_id("Build X").startswith("tdd-") and len(derive_thread_id("Build X")) == 20
    a, b = fresh_thread_id(), fresh_thread_id()
    assert a != b and a.startswith("tdd-") and len(a) == 20


def test_checkpoint_uri_defaults_to_postgres_url() -> None:
    assert checkpoint_uri(None) == Config.POSTGRES_URL
    assert checkpoint_uri(MEMORY_CHECKPOINT) == "memory"
    assert checkpoint_uri("postgresql://x") == "postgresql://x"


def test_options_defaults() -> None:
    assert SessionOptions() == SessionOptions(headless=False, checkpoint=None, permission_mode=None,
                                              settings_path=None, dangerously_skip_permissions=False)


def test_memory_checkpointer_is_recorded_and_warned(caplog: pytest.LogCaptureFixture) -> None:
    options = SessionOptions(checkpoint=MEMORY_CHECKPOINT, headless=True, permission_mode="acceptEdits",
                             settings_path="/s.json", dangerously_skip_permissions=True)
    with caplog.at_level(logging.WARNING):
        shell, _ = build(options)
    assert isinstance(shell.checkpointer, MemorySaver)
    assert "Session t checkpoints in memory" in caplog.text
    assert development_run_options(shell, options) == {
        "postgres_checkpointing": False, "permission_mode": "acceptEdits", "settings_path": "/s.json",
        "dangerously_skip_permissions": True}


def test_a_persistent_checkpointer_reaches_the_development_run(caplog: pytest.LogCaptureFixture) -> None:
    saver = MemorySaver()
    seen: list[Any] = []

    def fake_build(uri: Any) -> Any:
        seen.append(uri)
        return saver

    with patch.object(driver, "build_checkpointer", fake_build), \
            patch.object(driver, "is_persistent_checkpointer", lambda cp: True), caplog.at_level(logging.WARNING):
        shell, _ = build(SessionOptions(headless=True))
        assert development_run_options(shell, SessionOptions())["postgres_checkpointing"] is True
    assert seen == [Config.POSTGRES_URL]
    assert "checkpoints in memory" not in caplog.text


def test_default_workspace_is_the_production_one() -> None:
    made: list[str] = []

    def fake_workspace(thread_id: str) -> Any:
        made.append(thread_id)
        return "ws"

    with patch("app.cli.print.session_workspace", fake_workspace):
        shell, ws = build_session_shell("t-9", SessionOptions(checkpoint=MEMORY_CHECKPOINT))
    assert made == ["t-9"] and ws == "ws"
    assert isinstance(shell, SessionShell)


def test_headless_flag_reaches_the_shell() -> None:
    shell, _ = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT, headless=True), analyzer=question)
    run = drive_session(shell, "t", "req")
    assert run.values["status"] == SessionStatus.FAILED
    assert run.paused_at is None
    assert not run.planned


def test_interactive_drive_answers_interrupts() -> None:
    shell, _ = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT))
    asked: list[dict[str, Any]] = []

    def answer(payload: dict[str, Any]) -> str:
        asked.append(payload)
        return "/yes"

    run = drive_session(shell, "t", "req", answer)
    assert [p["type"] for p in asked] == ["checklist_approval"]
    assert run.planned and run.paused_at is None
    assert run.values["user_prompts"] == ["req", "/yes"]
    assert run.values["plan"] == ["A"]


def test_answer_none_pauses_and_a_later_drive_continues() -> None:
    shell, _ = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT))
    run = drive_session(shell, "t", "req", lambda payload: None)
    assert run.paused_at is not None and run.paused_at["type"] == "checklist_approval"
    assert run.values["status"] == SessionStatus.AWAITING_INPUT
    run = drive_session(shell, "t", None, lambda payload: "/yes")
    assert run.planned
    assert run.values["user_prompts"] == ["req", "/yes"]


def test_a_paused_thread_driven_headless_is_decided_by_the_shell() -> None:
    saver = MemorySaver()
    with patch.object(driver, "build_checkpointer", lambda uri: saver):
        shell, _ = build(SessionOptions())
        drive_session(shell, "t", "req", lambda payload: None)
        headless, _ = build(SessionOptions(headless=True))
    assert drive_session(headless, "t", None).planned


def test_a_finished_thread_is_reported_as_it_stands(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[str] = []

    def planner(spec: str, completed: Any) -> list[str]:
        calls.append(spec)
        return ["A"]

    shell, _ = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT, headless=True), planner=planner)
    first = drive_session(shell, "t", "req")
    with caplog.at_level(logging.INFO):
        again = drive_session(shell, "t", "other request")
    assert again.values == first.values
    assert len(calls) == 1
    assert "Session t already finished; reporting its checkpoint." in caplog.text


def test_an_unfinished_thread_continues(caplog: pytest.LogCaptureFixture) -> None:
    shell = MagicMock()
    shell.is_finished.return_value = False
    shell.has_session.return_value = True
    shell.pending_interrupts.return_value = []
    shell.get_state.return_value.values = {"status": SessionStatus.PLANNED}
    with caplog.at_level(logging.INFO):
        run = drive_session(shell, "t", None, lambda p: "/yes")
    shell.continue_session.assert_called_once_with("t")
    shell.run.assert_not_called()
    assert run.planned
    assert "Continuing session t from its last checkpoint." in caplog.text


def test_a_pending_interrupt_is_answered_not_continued() -> None:
    shell = MagicMock()
    shell.is_finished.return_value = False
    shell.has_session.return_value = True
    shell.pending_interrupts.side_effect = [[{"type": "clarification"}], [{"type": "clarification"}], []]
    shell.get_state.return_value.values = {}
    drive_session(shell, "t", None, lambda p: "EUR")
    shell.continue_session.assert_not_called()
    shell.resume.assert_called_once_with("t", "EUR")


def test_no_session_and_no_request_is_an_error() -> None:
    shell, _ = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT))
    with pytest.raises(ValueError, match="no session 't' to continue"):
        drive_session(shell, "t", None)


def test_session_run_planned() -> None:
    assert SessionRun(values={"status": SessionStatus.PLANNED}).planned is True
    assert SessionRun(values={"status": SessionStatus.FAILED}).planned is False
    assert SessionRun(values={}).paused_at is None


def test_answer_interrupts_without_an_answer_reports_the_state() -> None:
    shell = MagicMock()
    shell.get_state.return_value.values = {"status": "x"}
    assert answer_interrupts(shell, "t", None) == SessionRun(values={"status": "x"})
    shell.pending_interrupts.assert_not_called()


def test_rescope_session_reruns_the_analysis_and_answers_its_review() -> None:
    seen: list[Any] = []

    def planner(spec: str, completed: Any) -> list[str]:
        seen.append(completed)
        return ["B"]

    shell, _ = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT), planner=planner)
    drive_session(shell, "t", "req", lambda p: "/yes")
    asked: list[str] = []

    def answer(payload: dict[str, Any]) -> str:
        asked.append(payload["type"])
        return "/yes"

    run = rescope_session(shell, "t", "add B", ["A"], answer)
    assert run.planned and run.values["plan"] == ["B"]
    assert asked == ["checklist_approval"]
    assert seen[-1] == ["A"]


def test_rescope_session_can_pause_at_the_review() -> None:
    shell, _ = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT))
    drive_session(shell, "t", "req", lambda p: "/yes")
    run = rescope_session(shell, "t", "more", [], lambda p: None)
    assert run.paused_at is not None and not run.planned


def test_a_headless_session_end_to_end_through_development(python_workspace: Any, tmp_path: Any) -> None:
    """
    Driver -> shell -> `run_headless` with a scripted model on a real workspace: the session
    is complete only because the item's ledger observed Red then Green.
    """
    from langchain_core.messages import AIMessage

    from tests.test_phase0_baseline import CALC_IMPL, CALC_TEST, _call, _scripted_deps

    turns = [
        [_call("WriteFile", "c1", file_path="tests/test_calc.py", content=CALC_TEST)],
        [_call("RunTests", "c2", test_path="tests")],
        [_call("WriteFile", "c3", file_path="calculator.py", content=CALC_IMPL)],
        [_call("RunTests", "c4", test_path="tests")],
        [AIMessage(content="Red then green observed.")],
    ]
    options = SessionOptions(headless=True, checkpoint=MEMORY_CHECKPOINT, permission_mode="acceptEdits")
    shell, _ = build_session_shell(
        "tdd-e2e", options, workspace_factory=lambda thread_id: python_workspace, analyzer_fn=checklist,
        engineer_fn=lambda r, h: "# Spec: add", planner_fn=lambda s, c: ["add two numbers"],
    )
    run = drive_session(shell, "tdd-e2e", "Build a calculator")
    assert run.planned
    outcome = develop_headless(shell, "tdd-e2e", run.values, python_workspace, options,
                               deps=_scripted_deps(turns), project_root=tmp_path, home=tmp_path / "home",
                               managed_dir=tmp_path / "etc")
    assert outcome.completed and outcome.terminal == Terminal.COMPLETED
    assert [i.status for i in outcome.plan.items] == [ItemStatus.DONE]
    assert python_workspace.read_file("calculator.py") == CALC_IMPL


def test_the_checkpoint_option_reaches_build_checkpointer() -> None:
    seen: list[Any] = []

    def fake_build(uri: Any) -> Any:
        seen.append(uri)
        return MemorySaver()

    with patch.object(driver, "build_checkpointer", fake_build):
        build(SessionOptions(checkpoint="postgresql://elsewhere"))
    assert seen == ["postgresql://elsewhere"]


def test_pending_interrupts_are_read_for_the_thread() -> None:
    shell = MagicMock()
    shell.is_finished.return_value = False
    shell.has_session.return_value = True
    shell.pending_interrupts.return_value = []
    shell.get_state.return_value.values = {}
    drive_session(shell, "t-3", None, lambda p: "/yes")
    assert {c.args for c in shell.pending_interrupts.call_args_list} == {("t-3",)}
