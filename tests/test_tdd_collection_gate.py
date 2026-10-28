"""
A test file pytest does not collect checks nothing (`app/tdd/collection_gate.py`, user decision
2026-10-28): RED gets no evidence from it, and a GREEN or REFACTOR pass does not count.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.collection_gate import (
    JUNIT_REPORT_PATH,
    UNCOLLECTED_IS_NOT_PASSING,
    collected_files,
    junit_options,
    project_test_modules,
    uncollected_message,
    uncollected_test_files,
)
from app.tdd.coverage_gate import CoverageReading, CoverageStatus
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.run_tests_tool.run_tests_tool import (
    SuiteExecutionResult,
    build_run_tests_tool,
    run_suite,
)
from app.tool import tool_context_for
from tests.conftest import FakeWorkspace

TEST = "def test_a():\n    assert True\n"
MET = CoverageReading(CoverageStatus.MEASURED, 100.0)


def junit(*files: str) -> str:
    cases = "".join(f'<testcase classname="c" name="t" file="{f}" line="1"/>' for f in files)
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>'


def test_the_report_lives_in_the_protected_directory_and_names_files():
    assert JUNIT_REPORT_PATH == ".tddagents/coverage/junit.xml"
    assert junit_options() == "--junitxml=.tddagents/coverage/junit.xml -o junit_family=xunit1"


def test_collected_files_are_read_from_the_report():
    assert collected_files(junit("tests/test_a.py", "tests/./test_b.py", "tests/test_a.py")) == frozenset(
        {"tests/test_a.py", "tests/test_b.py"})
    assert collected_files('<testsuites><testsuite><testcase name="x"/></testsuite></testsuites>') == frozenset()


@pytest.mark.parametrize("report", [None, "", "not xml <"])
def test_no_readable_report_is_unknown(report: str | None):
    assert collected_files(report) is None


def test_project_test_modules_are_python_test_files_that_define_tests():
    ws = FakeWorkspace(files={
        "tests/test_a.py": TEST,
        "tests/tests_trivial.py": "import pytest\n\n@pytest.mark.parametrize('s', [''])\ndef test_x(s):\n    pass\n",
        "tests/test_cls.py": "class TestThing:\n    def it(self):\n        pass\n",
        "tests/test_async.py": "async def test_a():\n    pass\n",
        "tests/helpers.py": "def make():\n    return 1\n",
        "tests/__init__.py": "def test_never():\n    pass\n",
        "tests/conftest.py": "def test_never():\n    pass\n",
        "tests/data.txt": "def test_x(): pass\n",
        "test_root.py": TEST,
        "src/app.py": TEST,
        ".venv/lib/tests/test_dep.py": TEST,
        "tests/__pycache__/test_a.py": TEST,
    })
    assert project_test_modules(ws) == frozenset({
        "tests/test_a.py", "tests/tests_trivial.py", "tests/test_cls.py", "tests/test_async.py", "test_root.py"})


@pytest.mark.parametrize("scope, expected", [
    (".", {"tests/test_a.py", "tests/unit/test_b.py", "test_root.py"}),
    ("", {"tests/test_a.py", "tests/unit/test_b.py", "test_root.py"}),
    ("tests", {"tests/test_a.py", "tests/unit/test_b.py"}),
    ("tests/", {"tests/test_a.py", "tests/unit/test_b.py"}),
    ("tests/unit", {"tests/unit/test_b.py"}),
    ("tests/test_a.py", {"tests/test_a.py"}),
    ("tests/test_a.py::test_a", {"tests/test_a.py"}),
    ("test", set()),
])
def test_only_modules_within_the_runs_test_path_count(scope: str, expected: set[str]):
    ws = FakeWorkspace(files={"tests/test_a.py": TEST, "tests/unit/test_b.py": TEST, "test_root.py": TEST})
    assert project_test_modules(ws, scope) == frozenset(expected)


def test_an_unlistable_workspace_has_no_modules_to_check():
    class Broken(FakeWorkspace):
        def list_files(self, path: str = ".", depth: int = 1) -> Any:
            raise OSError("gone")

    class Unreadable(FakeWorkspace):
        def read_file(self, path: str) -> str:
            raise OSError("binary")

    assert project_test_modules(object()) is None
    assert project_test_modules(Broken()) is None
    assert project_test_modules(Unreadable(files={"tests/test_a.py": TEST})) == frozenset()
    assert uncollected_test_files(Broken(), ".", frozenset()) == ()


def test_uncollected_files_are_the_modules_the_report_lacks():
    ws = FakeWorkspace(files={"tests/test_b.py": TEST, "tests/test_a.py": TEST, "tests/tests_c.py": TEST})
    assert uncollected_test_files(ws, ".", frozenset({"tests/test_a.py"})) == ("tests/test_b.py", "tests/tests_c.py")
    assert uncollected_test_files(ws, ".", None) == ()
    assert uncollected_test_files(ws, "tests/test_a.py", frozenset({"tests/test_a.py"})) == ()


def test_the_message_names_the_files_and_the_way_out():
    assert uncollected_message(("tests/a.py", "tests/b.py")) == UNCOLLECTED_IS_NOT_PASSING.format(
        count=2, files="tests/a.py, tests/b.py")
    assert UNCOLLECTED_IS_NOT_PASSING == (
        "pytest did not collect {count} test file(s), so their tests never ran and this run does not count: "
        "{files}. Make every test file collectable in a plain RunTests run: name it as the project's pytest "
        "configuration expects (`python_files`, `testpaths`; by default test_*.py or *_test.py), or change that "
        "configuration. Then run RunTests again.")


# ── RunTests ────────────────────────────────────────────────────────────────

FILES = {"tests/test_a.py": TEST, "tests/tests_trivial.py": TEST}
ONLY_A = frozenset({"tests/test_a.py"})


def run(ledger: PhaseLedger, collected: frozenset[str] | None, exit_code: int = 0) -> tuple[Any, AppStateStore]:
    store = AppStateStore(AppState(phase_ledger=ledger))
    ctx = tool_context_for(store, workspace=FakeWorkspace(files=dict(FILES)))
    result = SuiteExecutionResult(exit_code=exit_code, coverage=MET, collected_files=collected)
    tool = build_run_tests_tool(lambda path: result)
    return asyncio.run(tool.call({}, ctx)), store


def test_red_gets_no_evidence_from_an_uncollected_test_file():
    res, store = run(PhaseLedger(), ONLY_A)
    assert store.get().phase_ledger == PhaseLedger()
    assert res.content.startswith(
        f"All tests passed.\n{uncollected_message(('tests/tests_trivial.py',))}\nNo test file changed")


@pytest.mark.parametrize("ledger, stays", [
    (PhaseLedger(phase=TddPhase.GREEN, tests_written=True), TddPhase.GREEN),
    (PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True), TddPhase.REFACTOR),
])
def test_a_pass_with_an_uncollected_test_file_does_not_count(ledger: PhaseLedger, stays: TddPhase):
    res, store = run(ledger, ONLY_A)
    after = store.get().phase_ledger
    assert after.phase == stays and after.green_passed is (stays == TddPhase.REFACTOR)
    assert after.refactor_passed is False
    assert res.content.startswith(
        f"Tests ran without failures, but {uncollected_message(('tests/tests_trivial.py',))}\n")


def test_a_failing_run_names_the_uncollected_files_too():
    res, store = run(PhaseLedger(phase=TddPhase.GREEN, tests_written=True), ONLY_A, exit_code=1)
    assert res.content.startswith(f"Tests failed (exit 1).\n{uncollected_message(('tests/tests_trivial.py',))}\n")
    assert store.get().phase_ledger.phase == TddPhase.GREEN


@pytest.mark.parametrize("collected", [frozenset(FILES), None])
def test_every_file_collected_or_an_unknown_report_checks_nothing(collected: frozenset[str] | None):
    res, store = run(PhaseLedger(phase=TddPhase.GREEN, tests_written=True), collected)
    assert res.content.startswith("All tests passed.\nGREEN is done.")
    assert store.get().phase_ledger.phase == TddPhase.REFACTOR


# ── real pytest: the live case ──────────────────────────────────────────────

def test_the_live_case_with_real_pytest(python_workspace):
    """`python_files = tests/test_*.py` leaves `tests/tests_trivial.py` uncollected."""
    python_workspace.write_file("pytest.ini", "[pytest]\ntestpaths = tests\npython_files = tests/test_*.py\n")
    python_workspace.write_file("tests/test_a.py", TEST)
    python_workspace.write_file("tests/tests_trivial.py", "def test_trivial():\n    assert False\n")
    result = run_suite(python_workspace, ".")
    assert result.exit_code == 0  # the failing test never ran
    assert result.collected_files == frozenset({"tests/test_a.py"})
    assert not python_workspace.exists(JUNIT_REPORT_PATH)

    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    ctx = tool_context_for(store, workspace=python_workspace)
    res = asyncio.run(build_run_tests_tool(None).call({}, ctx))
    assert "pytest did not collect 1 test file(s)" in res.content and "tests/tests_trivial.py" in res.content
    assert store.get().phase_ledger.phase == TddPhase.GREEN

    python_workspace.move("tests/tests_trivial.py", "tests/test_trivial.py")
    res = asyncio.run(build_run_tests_tool(None).call({}, ctx))
    assert res.exit_code == 1 and "pytest did not collect" not in res.content
