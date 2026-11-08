"""
Tests for RunTests loop tool and phase ledger updates (Part D2).

Since 2026-10-28 RunTests reports only what it observed, as claude-code's tools do: the exit code
with its meaning (upstream's `commandSemantics.ts` shape), the facts the reports recorded, the
ledger change with the phase's rule, then the raw output. Since 2026-11-03 it runs the project's
declared test command over the whole suite, reads the JUnit report it writes, and judges the run
with `app/tdd/run_judgement.py::judge`.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.coverage_gate import UNAVAILABLE, CoverageReading, CoverageStatus
from app.tdd.junit_report import FailedCase, JUnitReport
from app.tdd.ledger import PhaseLedger, TddPhase, TestRunRecord
from app.tdd.plan import TddPlan
from app.tdd.project_config import CoverageReportConfig, ProjectConfig
from app.tdd.run_judgement import exit_semantic
from app.tdd.run_tests_tool.run_tests_tool import (
    BACKGROUND_LEDGER,
    NO_CURRENT_ITEM_LEDGER,
    NO_OUTPUT,
    NO_TEST_COMMAND,
    NOT_EVIDENCE,
    PHASE_RULES,
    RunTests,
    RunTestsUnavailable,
    SuiteExecutionResult,
    _default_test_runner,
    build_run_tests_tool,
    failed_case_lines,
    report_environment,
    run_suite,
    run_tests_timeout_seconds,
)
from app.tdd.suite_fingerprint import fingerprint_test_files
from app.tool import tool_context_for
from app.workspace.base import CommandResult, WorkspaceTimeout
from tests.conftest import FakeWorkspace
from tests.plan_helpers import CURRENT_ITEM_PLAN, TEST_PROJECT_CONFIG

MET = CoverageReading(CoverageStatus.MEASURED, 95.0)
UNDER = CoverageReading(CoverageStatus.MEASURED, 70.0)
TESTED = {"tests/test_foo.py": "def test_foo():\n    pass\n"}
#: A report of one test that ran from the workspace's one test file.
ONE = JUnitReport(tests=1, files=frozenset({"tests/test_foo.py"}))
FAILED_ONE = JUnitReport(tests=1, failures=1, files=frozenset({"tests/test_foo.py"}))

RED_RULE = PHASE_RULES[TddPhase.RED]
GREEN_RULE = PHASE_RULES[TddPhase.GREEN]
REFACTOR_RULE = PHASE_RULES[TddPhase.REFACTOR]
NOT_MEASURED = "Coverage: not measured"
NO_REPORT_LINE = "JUnit report: none was written"
ONE_LINE = "JUnit report: 1 test(s), 0 failed, 0 errored, 0 skipped"
FAILED_LINE = "JUnit report: 1 test(s), 1 failed, 0 errored, 0 skipped"


def state(phase: TddPhase = TddPhase.RED, **kwargs: Any) -> AppStateStore:
    ledger = PhaseLedger(phase=phase, tests_written=phase != TddPhase.RED, green_passed=phase == TddPhase.REFACTOR)
    values: dict[str, Any] = {"tdd_plan": CURRENT_ITEM_PLAN, "project_config": TEST_PROJECT_CONFIG,
                              "phase_ledger": ledger, **kwargs}
    return AppStateStore(AppState(**values))


def ws_ctx(store: AppStateStore, files: dict[str, str] | None = None) -> Any:
    """A context whose workspace holds test files the item's baseline has not seen."""
    return tool_context_for(store, workspace=FakeWorkspace(files=dict(TESTED if files is None else files)))


def exit_line(code: int) -> str:
    return f"Exit code {code} ({exit_semantic(code).meaning})"


def run(result: SuiteExecutionResult, ctx: Any) -> Any:
    return asyncio.run(build_run_tests_tool(lambda c: result).call({}, ctx))


# ── RED ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("exit_code", "report", "line"), [(1, FAILED_ONE, FAILED_LINE), (0, ONE, ONE_LINE),
                                                           (1, None, NO_REPORT_LINE)])
def test_a_run_of_new_tests_ends_red_whatever_its_outcome(exit_code, report, line):
    """A failed run without a report (a compile or load error) is still a completed run (user decision)."""
    store = state()
    res = run(SuiteExecutionResult(exit_code=exit_code, stdout="out", stderr="Err", report=report), ws_ctx(store))
    assert (res.is_error, res.exit_code) == (False, exit_code)
    assert res.content == (
        f"{exit_line(exit_code)}\n{line}\n{NOT_MEASURED}\nTDD ledger: RED → GREEN. {RED_RULE}: they changed, and "
        "this run counts.\n\nout\nErr")
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, tests_written=True)


def test_unchanged_tests_keep_red():
    files = dict(TESTED)
    baseline = fingerprint_test_files(FakeWorkspace(files=files), TEST_PROJECT_CONFIG)
    store = state(tdd_item_test_baseline=baseline)
    res = run(SuiteExecutionResult(exit_code=1, stdout="out", report=FAILED_ONE), ws_ctx(store, files))
    assert res.content == (
        f"{exit_line(1)}\n{FAILED_LINE}\n{NOT_MEASURED}\nTDD ledger: still RED. {RED_RULE}: they have not changed."
        "\n\nout")
    assert store.get().phase_ledger == PhaseLedger()
    assert store.get().last_test_run is not None


def test_red_without_a_workspace_has_no_evidence_of_new_tests():
    store = state()
    run(SuiteExecutionResult(exit_code=0, report=ONE), tool_context_for(store))
    assert store.get().phase_ledger == PhaseLedger()


def test_red_gets_no_evidence_from_a_declared_test_file_that_did_not_run():
    files = {**TESTED, "tests/test_bar.py": "def test_bar():\n    pass\n"}
    store = state()
    res = run(SuiteExecutionResult(exit_code=0, report=ONE), ws_ctx(store, files))
    assert res.content.split("\n\n", 1)[0].splitlines()[2:] == [
        "Declared test files the run did not run: tests/test_bar.py", NOT_MEASURED,
        f"TDD ledger: still RED. {RED_RULE}: a declared test file did not run."]
    assert store.get().phase_ledger == PhaseLedger()


def test_the_fingerprint_is_compared_only_in_red():
    store = state(TddPhase.GREEN)
    ctx = ws_ctx(store)
    run(SuiteExecutionResult(exit_code=0, coverage=MET, report=ONE), ctx)
    assert ctx.workspace.command_log == []
    assert store.get().phase_ledger.phase == TddPhase.REFACTOR


# ── GREEN and REFACTOR ───────────────────────────────────────────────────────


def test_a_pass_in_green_moves_to_the_mandatory_refactor():
    store = state(TddPhase.GREEN)
    res = run(SuiteExecutionResult(exit_code=0, stdout="ok", coverage=UNDER, report=ONE, command="make test"),
              ws_ctx(store))
    assert res.content == (
        f"Command: make test\n{exit_line(0)}\n{ONE_LINE}\nCoverage: 70.0% (line + branch)\nTDD ledger: GREEN → "
        f"REFACTOR. {GREEN_RULE}: this run met it.\n\nok")
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True,
                                                   coverage=70.0)


def test_a_failure_in_green_stays_in_green():
    store = state(TddPhase.GREEN)
    ledger = store.get().phase_ledger
    res = run(SuiteExecutionResult(exit_code=1, report=FAILED_ONE), ws_ctx(store))
    assert res.content == (
        f"{exit_line(1)}\n{FAILED_LINE}\n{NOT_MEASURED}\nTDD ledger: still GREEN. {GREEN_RULE}: the test command "
        f"exited 1, 1 test(s) failed.\n\n{NO_OUTPUT}")
    assert store.get().phase_ledger == ledger


@pytest.mark.parametrize("phase", [TddPhase.GREEN, TddPhase.REFACTOR])
@pytest.mark.parametrize(("report", "unmet"), [
    (JUnitReport(tests=2, skipped=1, files=frozenset({"tests/test_foo.py"})), "1 test(s) were skipped"),
    (JUnitReport(tests=1, errors=1, files=frozenset({"tests/test_foo.py"})), "1 test(s) errored"),
    (JUnitReport(tests=1, files=frozenset()), "a declared test file did not run"),
])
def test_an_exit_zero_run_with_skips_errors_or_unrun_files_is_not_a_pass(phase, report, unmet):
    store = state(phase)
    ledger = store.get().phase_ledger
    res = run(SuiteExecutionResult(exit_code=0, report=report), ws_ctx(store))
    assert f"TDD ledger: still {phase.value}. {PHASE_RULES[phase]}: {unmet}." in res.content
    assert store.get().phase_ledger == ledger


REFACTOR_DONE = (f"TDD ledger: REFACTOR passed, so this sub-requirement's cycle is complete. {REFACTOR_RULE}: this "
                 "run met it.")


@pytest.mark.parametrize("result, coverage, status, passed", [
    (SuiteExecutionResult(exit_code=0, coverage=MET, report=ONE), "Coverage: 95.0% (line + branch)",
     REFACTOR_DONE, True),
    # Coverage is reported, never gated (user decision, 2026-10-28).
    (SuiteExecutionResult(exit_code=0, coverage=UNDER, report=ONE), "Coverage: 70.0% (line + branch)",
     REFACTOR_DONE, True),
    (SuiteExecutionResult(exit_code=0, report=ONE), NOT_MEASURED, REFACTOR_DONE, True),
    (SuiteExecutionResult(exit_code=0, coverage=CoverageReading(CoverageStatus.NO_CODE), report=ONE),
     "Coverage: no production code to measure", REFACTOR_DONE, True),
    (SuiteExecutionResult(exit_code=1, coverage=MET, report=FAILED_ONE), "Coverage: 95.0% (line + branch)",
     f"TDD ledger: still REFACTOR. {REFACTOR_RULE}: the test command exited 1, 1 test(s) failed.", False),
])
def test_refactor_is_judged_by_a_passing_run(result, coverage, status, passed):
    store = state(TddPhase.REFACTOR)
    res = run(result, ws_ctx(store))
    assert res.content.splitlines()[2:4] == [coverage, status]
    assert store.get().phase_ledger.refactor_passed is passed
    assert store.get().phase_ledger.is_cycle_complete is passed


# ── runs that are not evidence ───────────────────────────────────────────────


@pytest.mark.parametrize(("result", "report_text"), [
    (SuiteExecutionResult(exit_code=0, stdout="o"), NO_REPORT_LINE),
    (SuiteExecutionResult(exit_code=0, stdout="o", report=JUnitReport(tests=0)),
     "JUnit report: 0 test(s), 0 failed, 0 errored, 0 skipped"),
    (SuiteExecutionResult(exit_code=126, stdout="o"), NO_REPORT_LINE),
    (SuiteExecutionResult(exit_code=127, stdout="o"), NO_REPORT_LINE),
    (SuiteExecutionResult(exit_code=137, stdout="o", report=ONE), ONE_LINE),
])
@pytest.mark.parametrize("phase", list(TddPhase))
def test_non_evidence_runs_never_move_the_ledger(result, report_text, phase):
    """An exit 0 with no tests in a report, a missing command or a killed run can never confirm a phase."""
    store = state(phase)
    ledger = store.get().phase_ledger
    res = run(result, ws_ctx(store))
    assert (res.is_error, res.exit_code) == (True, result.exit_code)
    assert res.content == (f"{exit_line(result.exit_code)}\n{report_text}\n{NOT_MEASURED}\n{NOT_EVIDENCE}\n\no")
    assert store.get().phase_ledger == ledger
    assert store.get().last_test_run is None


def test_an_invalid_declaration_is_named_in_the_refusal():
    """An edit left `project.json` invalid (2026-11-08): the refusal says why there is no configuration."""
    store = state(project_config=None, project_config_issues=("`test` is required",))
    res = asyncio.run(build_run_tests_tool(lambda c: SuiteExecutionResult(exit_code=0)).call({}, ws_ctx(store)))
    assert res.is_error and res.content == (
        f"RunTests could not run the tests: {NO_TEST_COMMAND}. .tddagents/project.json is invalid, so it declares "
        "nothing: `test` is required.\nTDD ledger: unchanged.")


def test_without_a_declaration_runtests_fails_closed_while_an_item_is_current():
    """User decision 2026-11-03: no project configuration while an item is current is a refusal."""
    store = state(project_config=None)
    calls: list[Any] = []

    def recording(context: Any) -> SuiteExecutionResult:
        calls.append(context)
        return SuiteExecutionResult(exit_code=0)

    res = asyncio.run(build_run_tests_tool(recording).call({}, ws_ctx(store)))
    assert (res.is_error, res.content) == (
        True, f"RunTests could not run the tests: {NO_TEST_COMMAND}\nTDD ledger: unchanged.")
    assert calls == [] and store.get().phase_ledger == PhaseLedger()
    assert NO_TEST_COMMAND == "the project declares no test command (its configuration is approved with the plan)"
    # With no item current, the default runner still has nothing to run.
    res = asyncio.run(RunTests.call({}, tool_context_for(AppStateStore(), workspace=FakeWorkspace())))
    assert res.content == f"RunTests could not run the tests: {NO_TEST_COMMAND}\nTDD ledger: unchanged."


def test_without_a_current_item_the_run_is_reported_without_a_phase_rule():
    done = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True, refactor_passed=True)
    for plan in (TddPlan(), CURRENT_ITEM_PLAN.complete_current(done)):
        store = AppStateStore(AppState(tdd_plan=plan, phase_ledger=PhaseLedger(phase=TddPhase.RED)))
        res = run(SuiteExecutionResult(exit_code=0, stdout="out", report=ONE), ws_ctx(store))
        assert res.content == (
            f"{exit_line(0)}\n{ONE_LINE}\n{NOT_MEASURED}\n{NO_CURRENT_ITEM_LEDGER}\n\nout")


def test_a_report_that_names_no_files_is_not_a_pass_in_green():
    """Strict collection (2026-11-08): which files ran must be known in GREEN and REFACTOR."""
    store = state(TddPhase.GREEN)
    res = run(SuiteExecutionResult(exit_code=0, report=JUnitReport(tests=1)), ws_ctx(store))
    assert res.content.split("\n\n", 1)[0].splitlines()[2:] == [
        "Declared test files the run did not run: unknown (the JUnit report names no test files)", NOT_MEASURED,
        f"TDD ledger: still GREEN. {GREEN_RULE}: the JUnit report names no test files."]
    assert store.get().phase_ledger.phase == TddPhase.GREEN


# ── background workers and the record ───────────────────────────────────────


@pytest.mark.parametrize("result, is_error", [
    (SuiteExecutionResult(exit_code=0, stdout="out", report=ONE), False),
    (SuiteExecutionResult(exit_code=1, stdout="out", report=FAILED_ONE), False),
    (SuiteExecutionResult(exit_code=0, stdout="out", report=JUnitReport(tests=2, skipped=1)), False),
])
def test_a_background_run_reports_evidence_and_never_the_ledger(result, is_error):
    """`shareSetAppState: !isAsync`: a background worker's run makes no phase claim and writes nothing."""
    store = state(TddPhase.GREEN)
    before = store.get()
    ctx = dataclasses.replace(ws_ctx(store), shares_app_state=False)
    res = run(result, ctx)
    head = res.content.split("\n\n", 1)[0].splitlines()
    assert head[0] == exit_line(result.exit_code) and head[-1] == BACKGROUND_LEDGER
    assert "GREEN" not in res.content and "REFACTOR" not in res.content
    assert (res.is_error, res.exit_code) == (is_error, result.exit_code)
    assert store.get() is before


def test_a_background_run_that_is_not_evidence_is_an_error():
    ctx = dataclasses.replace(ws_ctx(state()), shares_app_state=False)
    res = run(SuiteExecutionResult(exit_code=127), ctx)
    assert res.is_error is True and NOT_EVIDENCE in res.content


def test_an_observed_run_is_recorded_beside_the_ledger_with_its_command_and_meaning():
    store = state()
    run(SuiteExecutionResult(exit_code=1, stdout="E   assert 1 == 2", stderr="warn", report=FAILED_ONE,
                             command="go test ./..."), ws_ctx(store))
    assert store.get().last_test_run == TestRunRecord(
        command="go test ./...", exit_code=1, stdout="E   assert 1 == 2", stderr="warn",
        meaning="the test command failed", phase=TddPhase.RED, tests=1,
        test_fingerprint=fingerprint_test_files(FakeWorkspace(files=dict(TESTED)), TEST_PROJECT_CONFIG))


def test_a_later_run_replaces_the_record():
    store = state()
    ctx = tool_context_for(store)
    results = iter([SuiteExecutionResult(exit_code=1, stdout="red", command="a"),
                    SuiteExecutionResult(exit_code=0, stdout="ok", report=ONE, command="b")])
    tool = build_run_tests_tool(lambda c: next(results))
    asyncio.run(tool.call({}, ctx))
    asyncio.run(tool.call({}, ctx))
    assert store.get().last_test_run == TestRunRecord(command="b", exit_code=0, stdout="ok",
                                                      meaning="the test command completed", phase=TddPhase.RED, tests=1)


# ── the tool and its runner seam ────────────────────────────────────────────


def test_default_instance_and_properties():
    from app.tdd.run_tests_tool.run_tests_tool import PROMPT_PATH
    from app.utils.frontmatter_parser import parse_markdown_frontmatter

    prompt = parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1]
    assert prompt.startswith("Runs the test command the approved plan declares for the project, over the whole "
                             "suite, and returns what it observed:")
    for t in (RunTests, build_run_tests_tool()):
        assert t.name == "RunTests"
        assert t.prompt == prompt
        assert t.input_schema == {"type": "object", "properties": {}}
        assert t.is_read_only({}) is True
        assert t.is_concurrency_safe({}) is False
        assert t.is_implementation_writer() is False
        assert t.is_test_writer() is False
        assert t.description({}) == "Run the project's test suite"
    assert "pytest" not in prompt and "sandbox" not in prompt
    for rule in PHASE_RULES.values():
        assert rule.split(" ends with ", 1)[1] in prompt
    assert all("pytest" not in text for text in (*PHASE_RULES.values(), NOT_EVIDENCE))


def test_the_phase_rules_and_the_non_evidence_line_read_exactly_this():
    assert PHASE_RULES == {
        TddPhase.RED: ("RED ends with a completed run of the test command (its tests pass or fail), in which no "
                       "declared test file is named as not run and no file the declaration does not cover ran, "
                       "after the sub-requirement's test files changed"),
        TddPhase.GREEN: ("GREEN ends with a run that exits 0, whose JUnit report records tests, none failed, "
                         "errored or skipped, and in which every declared test file is named as run (by the JUnit "
                         "report, else by the listTests command) and no file the declaration does not cover ran"),
        TddPhase.REFACTOR: ("REFACTOR ends with a run that exits 0, whose JUnit report records tests, none "
                            "failed, errored or skipped, and in which every declared test file is named as run (by "
                            "the JUnit report, else by the listTests command) and no file the declaration does not "
                            "cover ran"),
    }
    assert NOT_EVIDENCE == ("TDD ledger: unchanged. A run is evidence only when the test command completes: it "
                            "fails, or it exits 0 with a JUnit report that records tests; this one did not.")


def test_each_failed_or_errored_test_is_named_below_the_counts():
    report = JUnitReport(tests=3, failures=1, errors=1, failed_cases=(
        FailedCase("failure", "tests/a.test.ts", "adds", "tests/a.test.ts", "expected 1 to be 2"),
        FailedCase("error", "tests.test_b", "test_b", "tests/test_b.py", None)),
        files=frozenset({"tests/a.test.ts", "tests/test_b.py"}))
    files = {"tests/test_b.py": "", "tests/a.test.ts": ""}
    res = run(SuiteExecutionResult(exit_code=1, report=report), ws_ctx(state(TddPhase.GREEN), files))
    lines = res.content.splitlines()
    at = lines.index("JUnit report: 3 test(s), 1 failed, 1 errored, 0 skipped")
    assert lines[at + 1:at + 3] == ["Failed test: tests/a.test.ts > adds: expected 1 to be 2",
                                    "Errored test: tests.test_b > test_b (tests/test_b.py)"]
    assert lines[at + 3] == NOT_MEASURED


def test_no_report_names_no_failed_test():
    assert failed_case_lines(None) == [] and failed_case_lines(ONE) == []


def test_the_output_is_rendered_as_upstream_renders_a_command():
    store = state()
    res = run(SuiteExecutionResult(exit_code=1, stdout="\n  \nRan\n\n", stderr="   \n "), tool_context_for(store))
    assert res.content.endswith(f"{NOT_MEASURED}\nTDD ledger: still RED. {RED_RULE}: they have not changed.\n\nRan")
    res = run(SuiteExecutionResult(exit_code=1, stderr="only stderr"), tool_context_for(store))
    assert res.content.endswith("\n\nonly stderr")


def test_an_async_runner_and_an_unavailable_one():
    store = state()
    ctx = tool_context_for(store)

    async def async_runner(context: Any) -> SuiteExecutionResult:
        assert context is ctx
        return SuiteExecutionResult(exit_code=1, stdout="async")

    res = asyncio.run(build_run_tests_tool(async_runner).call({}, ctx))
    assert res.content.endswith("\n\nasync")

    def unavailable(context: Any) -> SuiteExecutionResult:
        raise RunTestsUnavailable("runner exploded")

    res = asyncio.run(build_run_tests_tool(unavailable).call({}, ctx))
    assert (res.is_error, res.content) == (
        True, "RunTests could not run the tests: runner exploded\nTDD ledger: unchanged.")


class _Wrap:
    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    def __getattr__(self, item: str) -> Any:
        return getattr(self._ctx, item)


def test_a_context_runner_is_used_sync_or_async():
    base = tool_context_for(AppStateStore())

    class Sync(_Wrap):
        def test_runner(self, c: Any) -> SuiteExecutionResult:
            assert c is self
            return SuiteExecutionResult(exit_code=0, stdout="s", stderr="e")

    class Async(_Wrap):
        async def test_runner(self, c: Any) -> SuiteExecutionResult:
            return SuiteExecutionResult(exit_code=2, stdout="a")

    class Custom(_Wrap):
        def test_runner(self, c: Any) -> Any:
            return type("R", (), {"exit_code": 42})()

    async def go() -> None:
        r = await _default_test_runner(Sync(base))  # type: ignore[arg-type]
        assert (r.exit_code, r.stdout, r.stderr) == (0, "s", "e")
        assert (await _default_test_runner(Async(base))).exit_code == 2  # type: ignore[arg-type]
        r = await _default_test_runner(Custom(base))  # type: ignore[arg-type]
        assert (r.exit_code, r.stdout, r.stderr) == (42, "", "")

    asyncio.run(go())


@pytest.mark.parametrize("bad_exit", [None, "1", 1.0, object()])
def test_context_runner_without_an_int_exit_code_is_unavailable(bad_exit):
    class NoCode(_Wrap):
        def test_runner(self, c: Any) -> Any:
            return type("R", (), {"exit_code": bad_exit})()

    with pytest.raises(RunTestsUnavailable, match="returned no exit code"):
        asyncio.run(_default_test_runner(NoCode(tool_context_for(AppStateStore()))))  # type: ignore[arg-type]


def test_a_non_callable_context_runner_falls_through_to_the_declaration():
    class NotCallable(_Wrap):
        test_runner = "nope"

    ctx = NotCallable(tool_context_for(AppStateStore(AppState(project_config=TEST_PROJECT_CONFIG))))
    with pytest.raises(RunTestsUnavailable, match="no workspace"):
        asyncio.run(_default_test_runner(ctx))  # type: ignore[arg-type]


# ── run_suite: the declared command and its reports ─────────────────────────


class _RecordingWorkspace:
    """A workspace that records the command, the environment and every report operation."""

    def __init__(self, exit_code: int = 0, exc: Exception | None = None,
                 reports: dict[str, str] | None = None, root: str | None = "/proj") -> None:
        self.calls: list[tuple[str, float | None, dict[str, str] | None]] = []
        self.sandboxed: list[bool] = []
        self.exit_code, self.exc = exit_code, exc
        self.reports = dict(reports or {})
        self.ops: list[tuple[str, str]] = []
        if root is not None:
            self.root = root

    def write_file(self, path: str, content: str) -> None:
        self.ops.append(("write", path))

    def read_file(self, path: str) -> str:
        self.ops.append(("read", path))
        if path in self.reports:
            return self.reports[path]
        raise FileNotFoundError(path)

    def delete_file(self, path: str) -> None:
        self.ops.append(("delete", path))

    def list_files(self, path: str = ".", depth: int = 1) -> list[Any]:
        return []

    def execute(self, cmd: str, timeout: float | None = None, env: dict[str, str] | None = None,
                sandbox: bool = False) -> CommandResult:
        self.calls.append((cmd, timeout, env))
        self.sandboxed.append(sandbox)
        if self.exc:
            raise self.exc
        return CommandResult(stdout="runner output", stderr="", exit_code=self.exit_code, duration=0.1,
                             workspace="local")


JUNIT = '<testsuite><testcase name="a" file="/proj/tests/test_a.py"/></testsuite>'
LCOV = "SF:/proj/calc.js\nDA:1,1\nDA:2,0\nend_of_record\nSF:/proj/tests/test_a.py\nDA:1,1\nend_of_record\n"


def test_run_suite_runs_the_declared_command_with_the_report_variables():
    config = ProjectConfig(test="npm test", test_files=("tests/",), junit_report="out/junit.xml",
                           coverage_report=CoverageReportConfig(format="lcov"))
    ws = _RecordingWorkspace(reports={"out/junit.xml": JUNIT, ".tddagents/reports/lcov.info": LCOV})
    result = run_suite(ws, config)
    assert ws.calls == [("npm test", 120.0, {"TDDAGENTS_JUNIT_REPORT": "/proj/out/junit.xml",
                                             "TDDAGENTS_COVERAGE_REPORT": "/proj/.tddagents/reports/lcov.info"})]
    assert result == SuiteExecutionResult(
        exit_code=0, stdout="runner output", coverage=CoverageReading(CoverageStatus.MEASURED, 50.0),
        report=JUnitReport(tests=1, files=frozenset({"tests/test_a.py"})), command="npm test")
    # Each report is discarded before the run (an override's stale one too), read back, then discarded.
    junit, lcov = "out/junit.xml", ".tddagents/reports/lcov.info"
    assert ws.ops == [("write", junit), ("delete", junit), ("write", lcov), ("delete", lcov),
                      ("read", junit), ("write", junit), ("delete", junit),
                      ("read", lcov), ("write", lcov), ("delete", lcov)]


def test_the_report_variables_without_a_coverage_report_or_a_root():
    config = ProjectConfig(test="t", test_files=("x",))
    assert report_environment(_RecordingWorkspace(root=None), config) == {
        "TDDAGENTS_JUNIT_REPORT": ".tddagents/reports/junit.xml"}
    ws = _RecordingWorkspace()
    result = run_suite(ws, config, timeout=5.0, env={"EXTRA": "1", "TDDAGENTS_JUNIT_REPORT": "overridden"})
    assert ws.calls == [("t", 5.0, {"EXTRA": "1", "TDDAGENTS_JUNIT_REPORT": "/proj/.tddagents/reports/junit.xml"})]
    assert (result.report, result.coverage) == (None, UNAVAILABLE)


def test_timeout_follows_the_bash_default(monkeypatch):
    monkeypatch.setenv("BASH_DEFAULT_TIMEOUT_MS", "30000")
    assert run_tests_timeout_seconds() == 30.0
    monkeypatch.delenv("BASH_DEFAULT_TIMEOUT_MS")
    assert run_tests_timeout_seconds() == 120.0


@pytest.mark.parametrize("exc", [RuntimeError("Sandbox crashed"), WorkspaceTimeout("slow")])
def test_workspace_failure_is_unavailable_not_red(exc):
    """A crashed or timed-out run used to be mapped to exit 1 and could confirm RED."""
    store = state()
    ctx = tool_context_for(store, workspace=_RecordingWorkspace(exc=exc))
    res = asyncio.run(build_run_tests_tool(None).call({}, ctx))
    assert res.is_error is True
    assert res.content == (f"RunTests could not run the tests: the test command could not be executed in the "
                           f"workspace: {exc}\nTDD ledger: unchanged.")
    assert store.get().phase_ledger == PhaseLedger()


def test_without_a_workspace_it_fails_closed():
    """The old offline fallback reported 'all tests passed' with nothing run."""
    for workspace in (None, object()):
        store = state()
        res = asyncio.run(RunTests.call({}, tool_context_for(store, workspace=workspace)))
        assert (res.is_error, res.exit_code) == (True, None)
        assert res.content == ("RunTests could not run the tests: no workspace is available to run the tests in\n"
                               "TDD ledger: unchanged.")
        assert store.get().phase_ledger == PhaseLedger()


def test_suite_execution_result_defaults():
    res = SuiteExecutionResult(exit_code=0)
    assert (res.exit_code, res.stdout, res.stderr, res.coverage, res.report, res.command, res.__test__) == (
        0, "", "", UNAVAILABLE, None, "", False)


NO_CAUSE_PHRASES = ("does not exist yet", "not installed", "not written", "missing code", "install", "should",
                    "must", "implement")


@pytest.mark.parametrize("result", [
    SuiteExecutionResult(exit_code=2, stdout="E   ModuleNotFoundError: No module named 'fastapi'"),
    SuiteExecutionResult(exit_code=1, stdout="1 failed", report=FAILED_ONE),
    SuiteExecutionResult(exit_code=0, stdout="1 passed, 1 skipped", report=JUnitReport(tests=2, skipped=1,
                                                                                       files=frozenset())),
    SuiteExecutionResult(exit_code=127),
])
@pytest.mark.parametrize("phase", list(TddPhase))
def test_runtests_states_no_cause_and_gives_no_advice(result, phase):
    """Everything above the raw output is an observation or a stated rule (2026-10-28)."""
    res = run(result, ws_ctx(state(phase)))
    head = res.content.split("\n\n", 1)[0]
    for phrase in NO_CAUSE_PHRASES:
        assert phrase not in head.lower(), (phrase, head)


# ── integration: the declared pytest command in a real LocalWorkspace ───────


def test_real_pytest_red_green_and_a_measured_refactor_in_a_local_workspace(python_workspace):
    ws = python_workspace
    store = state()
    ctx = tool_context_for(store, workspace=ws)
    tool = build_run_tests_tool(None)

    ws.write_file("tests/test_add.py", "from add import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    ws.write_file("add.py", "def add(a, b):\n    return 0\n")
    red = asyncio.run(tool.call({}, ctx))
    assert red.exit_code == 1 and red.is_error is False, red.content
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, tests_written=True, green_passed=False)

    # Half of `sign` is never run: GREEN passes, and the runner's own output shows the uncovered lines.
    ws.write_file("add.py", "def add(a, b):\n    return a + b\n\n\ndef sign(x):\n    if x < 0:\n        return -1\n"
                            "    return 1\n")
    green = asyncio.run(tool.call({}, ctx))
    assert green.exit_code == 0 and store.get().phase_ledger.phase == TddPhase.REFACTOR, green.content
    assert "JUnit report: 1 test(s), 0 failed, 0 errored, 0 skipped" in green.content
    assert "add.py" in green.content and "Missing" in green.content
    ws.write_file("tests/test_add.py", "from add import add, sign\n\ndef test_add():\n    assert add(2, 3) == 5\n\n"
                                       "def test_sign():\n    assert sign(-2) == -1\n    assert sign(2) == 1\n")
    done = asyncio.run(tool.call({}, ctx))
    assert done.exit_code == 0, done.content
    assert store.get().phase_ledger.is_cycle_complete
    assert store.get().phase_ledger.coverage == 100.0
    assert not ws.exists(".tddagents/reports/junit.xml") and not ws.exists(".tddagents/reports/coverage.xml")


def test_real_pytest_with_no_production_code_passes_refactor(python_workspace):
    store = state(TddPhase.REFACTOR)
    python_workspace.write_file("tests/test_t.py", "def test_t():\n    assert True\n")
    res = asyncio.run(build_run_tests_tool(None).call({}, tool_context_for(store, workspace=python_workspace)))
    assert "Coverage: no production code to measure\nTDD ledger: REFACTOR passed" in res.content, res.content
    assert store.get().phase_ledger.is_cycle_complete


def test_real_pytest_with_no_tests_cannot_confirm_red(python_workspace):
    """pytest exits 5: a failed run by the shell's reading, but RED needs the test files to have changed."""
    store = state()
    baseline = fingerprint_test_files(python_workspace, TEST_PROJECT_CONFIG)
    store.update(lambda s: dataclasses.replace(s, tdd_item_test_baseline=baseline))
    python_workspace.write_file("calc.py", "X = 1\n")
    res = asyncio.run(build_run_tests_tool(None).call({}, tool_context_for(store, workspace=python_workspace)))
    assert res.exit_code == 5 and res.is_error is False
    assert store.get().phase_ledger == PhaseLedger()


def test_real_pytest_missing_module_is_a_failed_run_that_confirms_red(python_workspace):
    """Load errors count as failures (user decision 2026-11-03); the output says nothing about why."""
    store = state()
    python_workspace.write_file("tests/test_api.py", "import fastapi_not_installed\n\ndef test_a():\n    assert True\n")
    res = asyncio.run(build_run_tests_tool(None).call({}, tool_context_for(store, workspace=python_workspace)))
    head, output = res.content.split("\n\n", 1)
    assert res.exit_code == 2 and res.is_error is False
    assert head.splitlines()[:2] == [f"Command: {TEST_PROJECT_CONFIG.test}", "Exit code 2 (the test command failed)"]
    assert "No module named 'fastapi_not_installed'" in output and "fastapi" not in head
    assert store.get().phase_ledger.tests_written is True


def test_real_pytest_a_declared_test_file_its_config_never_collects(python_workspace):
    """The palindrome case, generalized: the project's own pytest config leaves a declared test file out."""
    ws = python_workspace
    ws.write_file("pytest.ini", "[pytest]\npython_files = check_*.py\n")
    ws.write_file("tests/check_a.py", "def test_a():\n    assert True\n")
    ws.write_file("tests/test_trivial.py", "def test_trivial():\n    assert False\n")
    store = state(TddPhase.GREEN)
    ctx = tool_context_for(store, workspace=ws)
    res = asyncio.run(build_run_tests_tool(None).call({}, ctx))
    assert res.exit_code == 0, res.content
    assert "Declared test files the run did not run: tests/test_trivial.py" in res.content
    assert store.get().phase_ledger.phase == TddPhase.GREEN
    ws.write_file("pytest.ini", "[pytest]\n")
    res = asyncio.run(build_run_tests_tool(None).call({}, ctx))
    assert res.exit_code == 1 and "does not name" not in res.content
