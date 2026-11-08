"""
The project's `listTests` command (`app/tdd/test_listing.py`, user decision 2026-11-08): what its stdout
lists, when `run_suite` runs it, and how a failure is stated.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.project_config import DEFAULT_JUNIT_REPORT, ProjectConfig
from app.tdd.run_tests_tool.run_tests_tool import build_run_tests_tool, run_suite
from app.tdd.test_listing import TestListing, parse_test_listing, run_test_listing
from app.tool import tool_context_for
from app.workspace.base import CommandResult, WorkspaceTimeout
from tests.conftest import FakeWorkspace
from tests.plan_helpers import CURRENT_ITEM_PLAN

#: surefire: one suite per class, no file named anywhere.
SUREFIRE = ('<testsuite name="com.x.CalcTest"><testcase classname="com.x.CalcTest" name="adds"/>'
            '<testcase classname="com.x.CalcTest" name="subs"/></testsuite>')
NAMED = '<testsuite><testcase name="a" file="src/test/CalcTest.java"/></testsuite>'
TEST = "mvn test"
LIST = "list-tests"
CONFIG = ProjectConfig(test=TEST, test_files=("src/test/",), list_tests=LIST)
FILES = {"src/test/CalcTest.java": "", "src/test/OtherTest.java": "", "src/main/Calc.java": ""}


class _Project(FakeWorkspace):
    """A project whose test command writes `junit` and whose listing prints `listing`."""

    def __init__(self, junit: str | None, listing: CommandResult | Exception) -> None:
        super().__init__(kind="local", files=dict(FILES))
        self.root = "/proj"
        self.junit = junit
        self.listing = listing
        self.envs: list[tuple[str, dict[str, str] | None]] = []

    def execute(self, cmd: str, timeout: float | None = None, env: dict | None = None) -> CommandResult:
        self.envs.append((cmd, env))
        if cmd == LIST:
            if isinstance(self.listing, Exception):
                raise self.listing
            return self.listing
        if self.junit is not None:
            self.files[DEFAULT_JUNIT_REPORT] = self.junit
        return CommandResult(stdout="ran", stderr="", exit_code=0, duration=0.0, workspace="local")


def _listed(stdout: str, code: int = 0) -> CommandResult:
    return CommandResult(stdout=stdout, stderr="err" if code else "", exit_code=code, duration=0.0,
                         workspace="local")


@pytest.mark.parametrize(("stdout", "files"), [
    ("src/test/CalcTest.java\nsrc/test/OtherTest.java\n", {"src/test/CalcTest.java", "src/test/OtherTest.java"}),
    # Absolute paths inside the project are made relative; outside it they are ignored; blanks go.
    ("/proj/src/test/CalcTest.java\n\n  ./src/test/OtherTest.java  \n/elsewhere/X.java\n../up.java\n/proj\n",
     {"src/test/CalcTest.java", "src/test/OtherTest.java"}),
    ("src\\test\\Win.java\n", {"src/test/Win.java"}),
    ("", set()),
])
def test_the_listing_is_one_project_relative_path_per_line(stdout: str, files: set[str]):
    assert parse_test_listing(stdout, "/proj") == frozenset(files)


def test_without_a_root_only_relative_paths_are_listed():
    assert parse_test_listing("/abs/a.java\nb.java\n", None) == frozenset({"b.java"})


def test_a_listing_states_why_it_named_no_files():
    assert TestListing(command=LIST, exit_code=0, files=frozenset()).fact is None
    assert TestListing(command=LIST, exit_code=3).fact == "the listTests command exited 3"
    assert TestListing(command=LIST, exit_code=None, error="boom").fact == (
        "the listTests command could not be executed: boom")


def test_running_the_listing():
    assert run_test_listing(_Project(None, _listed("")), ProjectConfig(test=TEST, test_files=("x",)), timeout=1) is None
    ws = _Project(None, _listed("/proj/src/test/CalcTest.java\n"))
    listing = run_test_listing(ws, CONFIG, timeout=5.0, env={"A": "1"})
    assert listing == TestListing(command=LIST, exit_code=0, files=frozenset({"src/test/CalcTest.java"}),
                                  stdout="/proj/src/test/CalcTest.java\n")
    assert ws.envs == [(LIST, {"A": "1"})]
    failed = run_test_listing(_Project(None, _listed("src/test/CalcTest.java\n", 2)), CONFIG, timeout=5.0)
    assert failed == TestListing(command=LIST, exit_code=2, files=None, stdout="src/test/CalcTest.java\n",
                                 stderr="err")
    broken = run_test_listing(_Project(None, WorkspaceTimeout("slow")), CONFIG, timeout=5.0)
    assert broken == TestListing(command=LIST, exit_code=None, error="slow")
    unnamed = run_test_listing(_Project(None, RuntimeError()), CONFIG, timeout=5.0)
    assert unnamed is not None and unnamed.error == "RuntimeError"


def test_run_suite_runs_the_listing_only_when_the_report_names_no_files():
    ws = _Project(SUREFIRE, _listed("src/test/CalcTest.java\n"))
    result = run_suite(ws, CONFIG, timeout=7.0, env={"A": "1"})
    assert result.listing == TestListing(command=LIST, exit_code=0, files=frozenset({"src/test/CalcTest.java"}),
                                         stdout="src/test/CalcTest.java\n")
    # After the test command, with the same environment.
    assert [cmd for cmd, _ in ws.envs] == [TEST, LIST]
    assert ws.envs[0][1] == ws.envs[1][1] and ws.envs[1][1] is not None and ws.envs[1][1]["A"] == "1"
    named = _Project(NAMED, _listed("x"))
    assert run_suite(named, CONFIG).listing is None and [cmd for cmd, _ in named.envs] == [TEST]
    # No report, or a report without tests: nothing to list for.
    for junit in (None, "<testsuite/>"):
        quiet = _Project(junit, _listed("x"))
        assert run_suite(quiet, CONFIG).listing is None and [cmd for cmd, _ in quiet.envs] == [TEST]
    # A project that declares no listing.
    plain = _Project(SUREFIRE, _listed("x"))
    assert run_suite(plain, ProjectConfig(test=TEST, test_files=("src/test/",))).listing is None


def _green(ws: Any, config: ProjectConfig) -> tuple[Any, AppStateStore]:
    store = AppStateStore(AppState(tdd_plan=CURRENT_ITEM_PLAN, project_config=config,
                                   phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    res = asyncio.run(build_run_tests_tool(None).call({}, tool_context_for(store, workspace=ws)))
    return res, store


def test_run_tests_judges_a_surefire_run_by_its_listing():
    ws = _Project(SUREFIRE, _listed("src/test/CalcTest.java\nsrc/test/OtherTest.java\n"))
    res, store = _green(ws, CONFIG)
    assert f"listTests: {LIST}\n" in res.content
    assert "Declared test files" not in res.content
    assert store.get().phase_ledger.phase == TddPhase.REFACTOR


def test_run_tests_states_a_listing_that_failed_and_keeps_green():
    ws = _Project(SUREFIRE, _listed("", 1))
    res, store = _green(ws, CONFIG)
    lines = res.content.split("\n\n", 1)[0].splitlines()
    assert f"listTests: {LIST} (the listTests command exited 1)" in lines
    assert ("Declared test files the run did not run: unknown (the JUnit report names no test files; the listTests "
            "command exited 1)") in lines
    assert lines[-1].endswith(": the JUnit report names no test files, the listTests command exited 1.")
    assert store.get().phase_ledger.phase == TddPhase.GREEN


def test_run_tests_names_the_declared_files_a_listing_leaves_out():
    ws = _Project(SUREFIRE, _listed("src/test/CalcTest.java\n"))
    res, store = _green(ws, CONFIG)
    assert "Declared test files the run did not run: src/test/OtherTest.java\n" in res.content
    assert store.get().phase_ledger.phase == TddPhase.GREEN
