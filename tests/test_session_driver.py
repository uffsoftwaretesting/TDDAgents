"""
The session driver (`app/session/driver.py`): thread ids, options, and the headless session,
which opens in plan mode (the Analyst) and develops only an approved plan.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from app.cli.print import session_run_id
from app.query.transitions import Terminal
from app.session.driver import (
    SessionOptions,
    derive_thread_id,
    develop_headless,
    development_run_options,
    fresh_thread_id,
    session_exists,
)
from app.tdd.plan import ItemStatus, TddPlan, plan_path, save_plan
from tests.test_phase0_baseline import CALC_IMPL, CALC_TEST, _call, _scripted_deps
from tests.test_repl import Model, deps_for


def test_thread_ids() -> None:
    assert derive_thread_id("Build a calculator") == derive_thread_id("Build a calculator")
    assert derive_thread_id("a") != derive_thread_id("b")
    assert derive_thread_id("a").startswith("tdd-") and len(derive_thread_id("a")) == len("tdd-") + 16
    assert fresh_thread_id() != fresh_thread_id()
    assert fresh_thread_id().startswith("tdd-") and len(fresh_thread_id()) == len("tdd-") + 16


def test_options_defaults_and_what_the_run_is_told() -> None:
    assert SessionOptions() == SessionOptions(headless=False, permission_mode=None, settings_path=None,
                                              dangerously_skip_permissions=False)
    options = SessionOptions(permission_mode="plan", settings_path="/s.json", dangerously_skip_permissions=True)
    assert development_run_options(options) == {
        "permission_mode": "plan", "settings_path": "/s.json", "dangerously_skip_permissions": True}


def _options(tmp_path: Any) -> dict[str, Any]:
    return {"project_root": tmp_path, "home": tmp_path / "home", "managed_dir": tmp_path / "etc"}


PLAN_FILE = "# Plan\n\n## Context\nAdd two numbers.\n\n## Implementation Plan\n1. add two numbers\n"


def test_a_headless_session_plans_and_stops_unapproved(python_workspace: Any, tmp_path: Any) -> None:
    """As upstream's `-p`, `ExitPlanMode` is denied: the session plans, and nothing is developed."""
    seen: list[Any] = []

    async def write_plan(state: Any) -> list[Any]:
        seen.append(state.tool_context.session_plans.file_path())
        return [_call("WriteFile", "p1", file_path=str(seen[0]), content=PLAN_FILE)]

    turns: list[Any] = [write_plan, [_call("ExitPlanMode", "p2")], [AIMessage(content="The plan needs approval.")]]
    options = SessionOptions(headless=True, permission_mode="acceptEdits")
    assert session_exists("tdd-plan", python_workspace) is False
    outcome = develop_headless("tdd-plan", "Build a calculator", python_workspace, options,
                               deps=deps_for(Model(turns)), **_options(tmp_path))
    assert not outcome.planned and not outcome.completed
    assert outcome.plan.items == ()
    assert outcome.plan_file == str(seen[0])
    assert seen[0].read_text(encoding="utf-8") == PLAN_FILE
    assert session_exists("tdd-plan", python_workspace) is True


def test_a_headless_session_develops_its_approved_plan(python_workspace: Any, tmp_path: Any) -> None:
    """
    With an approved plan saved (the sidecar a resumed session finds), the session develops it, and
    it is complete only because the item's ledger observed Red, Green and a covered Refactor.
    """
    turns = [
        [_call("WriteFile", "c1", file_path="tests/test_calc.py", content=CALC_TEST)],
        [_call("RunTests", "c2", test_path="tests")],
        [_call("WriteFile", "c3", file_path="calculator.py", content=CALC_IMPL)],
        [_call("RunTests", "c4", test_path="tests")],
        [_call("RunTests", "c5", test_path="tests")],
        [AIMessage(content="Red, green and refactor observed.")],
    ]
    save_plan(plan_path(session_run_id("tdd-e2e"), str(python_workspace.root)), TddPlan.from_texts(["add two numbers"]))
    options = SessionOptions(headless=True, permission_mode="acceptEdits")
    outcome = develop_headless("tdd-e2e", "Build a calculator", python_workspace, options,
                               deps=_scripted_deps(turns), **_options(tmp_path))
    assert outcome.completed and outcome.terminal == Terminal.COMPLETED
    assert [i.status for i in outcome.plan.items] == [ItemStatus.DONE]
    assert python_workspace.read_file("calculator.py") == CALC_IMPL
