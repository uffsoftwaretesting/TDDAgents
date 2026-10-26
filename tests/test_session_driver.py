"""Phase 9: the entry point's session driver — building the shell and driving a thread."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.config.config import Config
from app.session import driver
from app.session.driver import (
    MEMORY_CHECKPOINT,
    SessionOptions,
    SessionRun,
    build_session_shell,
    checkpoint_uri,
    derive_thread_id,
    drive_session,
    failed_requirements,
    fresh_thread_id,
)
from app.session.shell import SessionShell
from app.session.state import PlanItemResult, SessionState, SessionStatus


def checklist(user_input: str, history: str) -> dict[str, Any]:
    return {"response": "Checklist. Can I proceed?", "needs_clarification": False, "has_checklist": True}


def question(user_input: str, history: str) -> dict[str, Any]:
    return {"response": "Which?", "needs_clarification": True, "has_checklist": False}


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict[str, Any]]] = []

    def __call__(self, sub_req: str, idx: int, state: SessionState, **kwargs: Any) -> PlanItemResult:
        self.calls.append((sub_req, idx, kwargs))
        return PlanItemResult(index=idx, sub_requirement=sub_req, status="success", terminal_reason="completed",
                              red_confirmed=True, green_passed=True)


def build(options: SessionOptions, analyzer: Any = checklist, runner: Any = None) -> tuple[SessionShell, Any, Any]:
    runner = runner or RecordingRunner()
    workspace = object()
    shell, ws = build_session_shell(
        "t", options, workspace_factory=lambda thread_id: workspace, loop_runner=runner, analyzer_fn=analyzer,
        engineer_fn=lambda r, h: "SPEC", planner_fn=lambda s: ["A"],
    )
    assert ws is workspace
    return shell, ws, runner


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
        shell, ws, runner = build(options)
    assert isinstance(shell.checkpointer, MemorySaver)
    assert "Session t checkpoints in memory" in caplog.text
    drive_session(shell, "t", "req")
    (_, _, kwargs), = runner.calls
    assert kwargs == {"workspace": ws, "postgres_checkpointing": False, "permission_mode": "acceptEdits",
                      "settings_path": "/s.json", "dangerously_skip_permissions": True}


def test_a_persistent_checkpointer_reaches_the_runner(caplog: pytest.LogCaptureFixture) -> None:
    saver = MemorySaver()
    seen: list[Any] = []

    def fake_build(uri: Any) -> Any:
        seen.append(uri)
        return saver

    with patch.object(driver, "build_checkpointer", fake_build), \
            patch.object(driver, "is_persistent_checkpointer", lambda cp: True), caplog.at_level(logging.WARNING):
        shell, _, runner = build(SessionOptions(headless=True))
    assert seen == [Config.POSTGRES_URL]
    assert "checkpoints in memory" not in caplog.text
    drive_session(shell, "t", "req")
    assert runner.calls[0][2]["postgres_checkpointing"] is True


def test_default_workspace_and_runner_are_the_production_ones() -> None:
    made: list[str] = []

    def fake_workspace(thread_id: str) -> Any:
        made.append(thread_id)
        return "ws"

    with patch("app.loop.runner.session_workspace", fake_workspace):
        shell, ws = build_session_shell("t-9", SessionOptions(checkpoint=MEMORY_CHECKPOINT))
    assert made == ["t-9"] and ws == "ws"
    assert isinstance(shell, SessionShell)


def test_headless_flag_reaches_the_shell() -> None:
    shell, _, _ = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT, headless=True), analyzer=question)
    run = drive_session(shell, "t", "req")
    assert run.values["status"] == SessionStatus.FAILED
    assert run.paused_at is None
    assert not run.completed


def test_interactive_drive_answers_interrupts() -> None:
    shell, _, runner = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT))
    asked: list[dict[str, Any]] = []

    def answer(payload: dict[str, Any]) -> str:
        asked.append(payload)
        return "/yes"

    run = drive_session(shell, "t", "req", answer)
    assert [p["type"] for p in asked] == ["checklist_approval"]
    assert run.completed and run.paused_at is None
    assert run.values["user_prompts"] == ["req", "/yes"]
    assert len(runner.calls) == 1


def test_answer_none_pauses_and_a_later_drive_continues() -> None:
    shell, _, runner = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT))
    run = drive_session(shell, "t", "req", lambda payload: None)
    assert run.paused_at is not None and run.paused_at["type"] == "checklist_approval"
    assert run.values["status"] == SessionStatus.AWAITING_INPUT
    assert runner.calls == []
    run = drive_session(shell, "t", None, lambda payload: "/yes")
    assert run.completed
    assert run.values["user_prompts"] == ["req", "/yes"]


def test_a_paused_thread_driven_headless_is_decided_by_the_shell() -> None:
    saver = MemorySaver()
    with patch.object(driver, "build_checkpointer", lambda uri: saver):
        shell, _, _ = build(SessionOptions())
        drive_session(shell, "t", "req", lambda payload: None)
        headless, _, _ = build(SessionOptions(headless=True))
    assert drive_session(headless, "t", None).completed


def test_a_finished_thread_is_reported_as_it_stands(caplog: pytest.LogCaptureFixture) -> None:
    shell, _, runner = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT, headless=True))
    first = drive_session(shell, "t", "req")
    with caplog.at_level(logging.INFO):
        again = drive_session(shell, "t", "other request")
    assert again.values == first.values
    assert len(runner.calls) == 1
    assert "Session t already finished; reporting its checkpoint." in caplog.text


def test_an_unfinished_thread_continues(caplog: pytest.LogCaptureFixture) -> None:
    shell = MagicMock()
    shell.is_finished.return_value = False
    shell.has_session.return_value = True
    shell.pending_interrupts.return_value = []
    shell.get_state.return_value.values = {"status": SessionStatus.COMPLETED}
    with caplog.at_level(logging.INFO):
        run = drive_session(shell, "t", None, lambda p: "/yes")
    shell.continue_session.assert_called_once_with("t")
    shell.run.assert_not_called()
    assert run.completed
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
    shell, _, _ = build(SessionOptions(checkpoint=MEMORY_CHECKPOINT))
    with pytest.raises(ValueError, match="no session 't' to continue"):
        drive_session(shell, "t", None)


def test_session_run_completed() -> None:
    assert SessionRun(values={"status": SessionStatus.COMPLETED}).completed is True
    assert SessionRun(values={"status": SessionStatus.FAILED}).completed is False
    assert SessionRun(values={}).paused_at is None


def test_failed_requirements() -> None:
    values = {"subreq_results": [
        {"status": "success", "sub_requirement": "A"},
        {"status": "failed", "sub_requirement": "B", "error_message": "Terminated early: model_error",
         "terminal_reason": "model_error"},
        {"status": "failed", "sub_requirement": "C", "error_message": None, "terminal_reason": "hook_stopped"},
    ]}
    assert failed_requirements(values) == [
        {"requirement": "B", "reason": "Terminated early: model_error"},
        {"requirement": "C", "reason": "hook_stopped"},
    ]
    assert failed_requirements({}) == []
    assert failed_requirements({"subreq_results": [{"status": "failed"}]}) == [{"requirement": "", "reason": None}]


def test_a_headless_session_end_to_end_through_the_real_runner(python_workspace: Any, tmp_path: Any) -> None:
    """
    Driver -> shell -> `tdd_loop_runner` with a scripted model on a real workspace: the
    session completes only because the item's ledger observed Red then Green.
    """
    import functools

    from langchain_core.messages import AIMessage

    from app.loop.runner import tdd_loop_runner
    from tests.test_phase0_baseline import CALC_IMPL, CALC_TEST, _call, _scripted_deps

    turns = [
        [_call("WriteFile", "c1", file_path="tests/test_calc.py", content=CALC_TEST)],
        [_call("RunTests", "c2", test_path="tests")],
        [_call("WriteFile", "c3", file_path="calculator.py", content=CALC_IMPL)],
        [_call("RunTests", "c4", test_path="tests")],
        [AIMessage(content="Red then green observed.")],
    ]
    runner = functools.partial(tdd_loop_runner, deps=_scripted_deps(turns), project_root=tmp_path,
                               home=tmp_path / "home", managed_dir=tmp_path / "etc")
    shell, _ = build_session_shell(
        "tdd-e2e", SessionOptions(headless=True, checkpoint=MEMORY_CHECKPOINT, permission_mode="acceptEdits"),
        workspace_factory=lambda thread_id: python_workspace, loop_runner=runner, analyzer_fn=checklist,
        engineer_fn=lambda r, h: "# Spec: add", planner_fn=lambda s: ["add two numbers"],
    )
    run = drive_session(shell, "tdd-e2e", "Build a calculator")
    assert run.completed
    (item,) = run.values["subreq_results"]
    assert (item["status"], item["terminal_reason"], item["red_confirmed"], item["green_passed"]) == (
        "success", "completed", True, True)
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
