"""
Tests for RunTests loop tool and phase ledger updates (Part D2).

Since 2026-10-28 RunTests reports only what it observed, as claude-code's tools do: the exit code
with pytest's meaning (`PYTEST_EXIT_SEMANTICS`, upstream's `commandSemantics.ts` shape), the facts
pytest's reports recorded, the ledger change with the phase's rule, then the raw output. A live run
whose RunTests said "the code under test does not exist yet" for a missing package is why.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.state.app_state_store import AppState, AppStateStore
from app.tdd.coverage_gate import (
    COVERAGE_CONFIG,
    COVERAGE_CONFIG_PATH,
    COVERAGE_REPORT_PATH,
    UNAVAILABLE,
    CoverageReading,
    CoverageStatus,
)
from app.tdd.ledger import PhaseLedger, TddPhase, TestRunRecord
from app.tdd.run_tests_tool.run_tests_tool import (
    BACKGROUND_LEDGER,
    NO_OUTPUT,
    NOT_EVIDENCE,
    PHASE_RULES,
    PYTEST_EXIT_SEMANTICS,
    ExitSemantic,
    Outcome,
    RunTests,
    RunTestsUnavailable,
    SuiteExecutionResult,
    build_run_tests_tool,
    exit_semantic,
    pytest_command,
    run_outcome,
    run_tests_timeout_seconds,
)
from app.tdd.suite_fingerprint import fingerprint_test_files
from app.tool import tool_context_for
from app.workspace.base import CommandResult, WorkspaceTimeout
from tests.conftest import FakeWorkspace

MET = CoverageReading(CoverageStatus.MEASURED, 95.0)
UNDER = CoverageReading(CoverageStatus.MEASURED, 70.0)
TESTED = {"tests/test_foo.py": "def test_foo():\n    pass\n"}


def ws_ctx(store: AppStateStore, files: dict[str, str] | None = None) -> Any:
    """A context whose workspace holds test files the item's baseline has not seen."""
    return tool_context_for(store, workspace=FakeWorkspace(files=dict(TESTED if files is None else files)))


RED_RULE = PHASE_RULES[TddPhase.RED]
GREEN_RULE = PHASE_RULES[TddPhase.GREEN]
REFACTOR_RULE = PHASE_RULES[TddPhase.REFACTOR]
NOT_MEASURED = "Coverage: not measured"


def exit_line(code: int) -> str:
    return f"Exit code {code} (pytest: {exit_semantic(code).meaning})"


@pytest.mark.parametrize("exit_code", [1, 0])
def test_a_run_of_new_tests_ends_red_whatever_its_outcome(exit_code):
    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
    tool = build_run_tests_tool(lambda path: SuiteExecutionResult(exit_code=exit_code, stdout="out", stderr="Err"))
    res = asyncio.run(tool.call({"test_path": "tests/test_foo.py"}, ws_ctx(store)))
    assert (res.is_error, res.exit_code) == (False, exit_code)
    assert res.content == (
        f"{exit_line(exit_code)}\n{NOT_MEASURED}\nTDD ledger: RED → GREEN. {RED_RULE}: they changed, and this run "
        "counts.\n\nout\nErr")
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, tests_written=True)


def test_unchanged_tests_keep_red():
    files = dict(TESTED)
    baseline = fingerprint_test_files(FakeWorkspace(files=files))
    store = AppStateStore(AppState(tdd_item_test_baseline=baseline))
    tool = build_run_tests_tool(lambda path: SuiteExecutionResult(exit_code=1, stdout="out"))
    res = asyncio.run(tool.call({}, ws_ctx(store, files)))
    assert res.content == (
        f"{exit_line(1)}\n{NOT_MEASURED}\nTDD ledger: still RED. {RED_RULE}: they have not changed.\n\nout")
    assert store.get().phase_ledger == PhaseLedger()
    assert store.get().last_test_run is not None


def test_red_without_a_workspace_has_no_evidence_of_new_tests():
    store = AppStateStore()
    asyncio.run(build_run_tests_tool(lambda p: SuiteExecutionResult(exit_code=0)).call({}, tool_context_for(store)))
    assert store.get().phase_ledger == PhaseLedger()


def test_the_fingerprint_is_compared_only_in_red():
    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    ctx = ws_ctx(store)
    asyncio.run(build_run_tests_tool(lambda p: SuiteExecutionResult(exit_code=0, coverage=MET)).call({}, ctx))
    assert ctx.workspace.command_log == []
    assert store.get().phase_ledger.phase == TddPhase.REFACTOR


def test_a_pass_in_green_moves_to_the_mandatory_refactor():
    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    res = asyncio.run(build_run_tests_tool(
        lambda path: SuiteExecutionResult(exit_code=0, stdout="ok", coverage=UNDER)).call({}, ws_ctx(store)))
    assert res.content == (
        f"{exit_line(0)}\nCoverage: 70.0% (line + branch)\nTDD ledger: GREEN → REFACTOR. {GREEN_RULE}: this run "
        "met it.\n\nok")
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True,
                                                   coverage=70.0)


def test_a_failure_in_green_stays_in_green():
    ledger = PhaseLedger(phase=TddPhase.GREEN, tests_written=True)
    store = AppStateStore(AppState(phase_ledger=ledger))
    res = asyncio.run(build_run_tests_tool(lambda path: SuiteExecutionResult(exit_code=1)).call({}, ws_ctx(store)))
    assert res.content == (
        f"{exit_line(1)}\n{NOT_MEASURED}\nTDD ledger: still GREEN. {GREEN_RULE}: this run failed.\n\n{NO_OUTPUT}")
    assert store.get().phase_ledger == ledger


REFACTORING = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True)
REFACTOR_DONE = (f"TDD ledger: REFACTOR passed, so this sub-requirement's cycle is complete. {REFACTOR_RULE}: this "
                 "run met it.")


@pytest.mark.parametrize("result, coverage, status, passed", [
    (SuiteExecutionResult(exit_code=0, coverage=MET), "Coverage: 95.0% (line + branch)", REFACTOR_DONE, True),
    # Coverage is reported, never gated (user decision, 2026-10-28).
    (SuiteExecutionResult(exit_code=0, coverage=UNDER), "Coverage: 70.0% (line + branch)", REFACTOR_DONE, True),
    (SuiteExecutionResult(exit_code=0), NOT_MEASURED, REFACTOR_DONE, True),
    (SuiteExecutionResult(exit_code=0, coverage=CoverageReading(CoverageStatus.NO_CODE)),
     "Coverage: no production code to measure", REFACTOR_DONE, True),
    (SuiteExecutionResult(exit_code=1, coverage=MET), "Coverage: 95.0% (line + branch)",
     f"TDD ledger: still REFACTOR. {REFACTOR_RULE}: this run failed.", False),
])
def test_refactor_is_judged_by_a_passing_run(result, coverage, status, passed):
    store = AppStateStore(AppState(phase_ledger=REFACTORING))
    res = asyncio.run(build_run_tests_tool(lambda path: result).call({}, ws_ctx(store)))
    assert res.content.splitlines()[1:3] == [coverage, status]
    assert store.get().phase_ledger.refactor_passed is passed
    assert store.get().phase_ledger.is_cycle_complete is passed


def test_exit_2_without_recorded_collection_errors_is_not_evidence():
    async def go():
        ledger = PhaseLedger(phase=TddPhase.RED, tests_written=False, green_passed=False)
        store = AppStateStore(AppState(phase_ledger=ledger))
        ctx = tool_context_for(store)

        async def async_runner(path, context):
            assert context is ctx
            return SuiteExecutionResult(exit_code=2, stdout="Syntax error", stderr="Failed")

        res = await build_run_tests_tool(async_runner).call({"test_path": "tests/test_bar.py"}, ctx)
        assert res.is_error is True
        assert res.exit_code == 2
        assert res.content == f"{exit_line(2)}\n{NOT_MEASURED}\n{NOT_EVIDENCE}\n\nSyntax error\nFailed"
        assert store.get().phase_ledger == ledger

    asyncio.run(go())


@pytest.mark.parametrize("code, meaning", [
    (2, "test execution was interrupted"),
    (3, "pytest hit an internal error"),
    (4, "pytest was invoked incorrectly (usage error)"),
    (5, "no tests were collected"),
    (127, "pytest exited with code 127"),
    (-9, "pytest exited with code -9"),
])
@pytest.mark.parametrize("phase", list(TddPhase))
def test_non_evidence_codes_never_move_the_ledger(code, meaning, phase):
    """'No tests collected' can never confirm RED; only the evidence outcomes count."""
    ledger = PhaseLedger(phase=phase, tests_written=phase != TddPhase.RED,
                         green_passed=phase == TddPhase.REFACTOR)
    store = AppStateStore(AppState(phase_ledger=ledger))
    tool = build_run_tests_tool(lambda path: SuiteExecutionResult(exit_code=code, stdout="o"))
    res = asyncio.run(tool.call({}, tool_context_for(store)))
    assert res.is_error is True
    assert res.exit_code == code
    assert res.content == f"Exit code {code} (pytest: {meaning})\n{NOT_MEASURED}\n{NOT_EVIDENCE}\n\no"
    assert store.get().phase_ledger == ledger
    assert store.get().last_test_run is None


def test_the_exit_code_table_is_pytests_documented_codes():
    """Upstream's `COMMAND_SEMANTICS` shape: meaning, is_error and the TDD outcome, from the code alone."""
    assert PYTEST_EXIT_SEMANTICS == {
        0: ExitSemantic("all tests passed", False, Outcome.PASSED),
        1: ExitSemantic("some tests failed", False, Outcome.FAILED),
        2: ExitSemantic("test execution was interrupted", True, None),
        3: ExitSemantic("pytest hit an internal error", True, None),
        4: ExitSemantic("pytest was invoked incorrectly (usage error)", True, None),
        5: ExitSemantic("no tests were collected", True, None),
    }
    assert exit_semantic(9) == ExitSemantic("pytest exited with code 9", True, None)


@pytest.mark.parametrize("result, expected", [
    (SuiteExecutionResult(exit_code=0), (Outcome.PASSED, False)),
    (SuiteExecutionResult(exit_code=1), (Outcome.FAILED, False)),
    (SuiteExecutionResult(exit_code=2), (None, True)),
    (SuiteExecutionResult(exit_code=2, collection_errors=frozenset()), (None, True)),
    (SuiteExecutionResult(exit_code=2, collection_errors=frozenset({"tests/test_a.py"})),
     (Outcome.COLLECTION_ERRORS, False)),
    # Only exit 2 is read with the report: pytest's other codes keep their own meaning.
    (SuiteExecutionResult(exit_code=1, collection_errors=frozenset({"tests/test_a.py"})), (Outcome.FAILED, False)),
    (SuiteExecutionResult(exit_code=4, collection_errors=frozenset({"tests/test_a.py"})), (None, True)),
])
def test_the_run_outcome(result, expected):
    assert run_outcome(result) == expected


def test_default_instance_and_properties():
    from app.tdd.run_tests_tool.run_tests_tool import PROMPT_PATH
    from app.utils.frontmatter_parser import parse_markdown_frontmatter

    RUN_TESTS_PROMPT = parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1]
    assert RUN_TESTS_PROMPT.startswith("Runs pytest in the workspace and returns what it observed:")
    built_instance = build_run_tests_tool()
    for t in (RunTests, built_instance):
        assert t.name == "RunTests"
        assert t.prompt == RUN_TESTS_PROMPT
        assert t.input_schema == {
            "type": "object",
            "properties": {
                "test_path": {
                    "type": "string",
                    "description": "File or directory to run. '.' runs the whole suite.",
                    "default": ".",
                }
            },
        }
        assert t.is_read_only({}) is True
        assert t.is_concurrency_safe({}) is False
        assert t.is_implementation_writer() is False
        assert t.is_test_writer() is False
        assert t.description({"test_path": "tests/test_x.py"}) == "Run tests in tests/test_x.py"
        assert t.description({}) == "Run tests in ."
    assert "workspace" in RUN_TESTS_PROMPT and "sandbox" not in RUN_TESTS_PROMPT
    for rule in PHASE_RULES.values():
        assert rule.split(" ends with ", 1)[1] in RUN_TESTS_PROMPT


def test_fails_closed_without_a_workspace():
    """The old offline fallback reported 'all tests passed' with nothing run."""
    ledger = PhaseLedger(phase=TddPhase.RED)
    store = AppStateStore(AppState(phase_ledger=ledger))
    res = asyncio.run(RunTests.call({}, tool_context_for(store)))
    assert res.is_error is True
    assert res.exit_code is None
    assert res.content == ("RunTests could not run the tests: no workspace is available to run the tests in\n"
                           "TDD ledger: unchanged.")
    assert store.get().phase_ledger == ledger


def test_suite_execution_result_defaults():
    res = SuiteExecutionResult(exit_code=0)
    assert (res.exit_code, res.stdout, res.stderr, res.coverage, res.__test__) == (0, "", "", UNAVAILABLE, False)
    assert (res.skipped, res.collected_files, res.collection_errors) == (None, None, None)


class _Wrap:
    def __init__(self, ctx):
        self._ctx = ctx

    def __getattr__(self, item):
        return getattr(self._ctx, item)


def test_default_test_runner_context_runner_shapes():
    async def go():
        from app.tdd.run_tests_tool.run_tests_tool import _default_test_runner

        base = tool_context_for(AppStateStore())

        class Sync2(_Wrap):
            def test_runner(self, path, c):
                assert c is self
                return SuiteExecutionResult(exit_code=0, stdout=f"s2 {path}", stderr="e")

        class Sync1(_Wrap):
            def test_runner(self, path):
                return SuiteExecutionResult(exit_code=1, stdout=f"s1 {path}")

        class Async2(_Wrap):
            async def test_runner(self, path, c):
                return SuiteExecutionResult(exit_code=2, stdout="a2")

        class Async1(_Wrap):
            async def test_runner(self, path):
                return SuiteExecutionResult(exit_code=3, stdout="a1")

        class Custom(_Wrap):
            def test_runner(self, path):
                return type("R", (), {"exit_code": 42})()

        r = await _default_test_runner("t.py", Sync2(base))  # type: ignore[arg-type]
        assert (r.exit_code, r.stdout, r.stderr) == (0, "s2 t.py", "e")
        assert (await _default_test_runner("t.py", Sync1(base))).stdout == "s1 t.py"  # type: ignore[arg-type]
        assert (await _default_test_runner("t.py", Async2(base))).exit_code == 2  # type: ignore[arg-type]
        assert (await _default_test_runner("t.py", Async1(base))).exit_code == 3  # type: ignore[arg-type]
        r = await _default_test_runner("t.py", Custom(base))  # type: ignore[arg-type]
        assert (r.exit_code, r.stdout, r.stderr) == (42, "", "")

    asyncio.run(go())


@pytest.mark.parametrize("bad_exit", [None, "1", 1.0, object()])
def test_context_runner_without_an_int_exit_code_is_unavailable(bad_exit):
    from app.tdd.run_tests_tool.run_tests_tool import _default_test_runner

    class NoCode(_Wrap):
        def test_runner(self, path):
            return type("R", (), {"exit_code": bad_exit})()

    with pytest.raises(RunTestsUnavailable, match="returned no exit code"):
        asyncio.run(_default_test_runner("t.py", NoCode(tool_context_for(AppStateStore()))))  # type: ignore[arg-type]


def test_non_callable_context_runner_falls_through_to_workspace_rule():
    from app.tdd.run_tests_tool.run_tests_tool import _default_test_runner

    class NotCallable(_Wrap):
        test_runner = "nope"

    with pytest.raises(RunTestsUnavailable, match="no workspace"):
        asyncio.run(_default_test_runner("t.py", NotCallable(tool_context_for(AppStateStore()))))  # type: ignore


STAYS_RED = f"TDD ledger: still RED. {RED_RULE}: they have not changed."


def test_call_argument_handling_and_formatting():
    async def go():
        store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
        ctx = tool_context_for(store)
        seen: list[str] = []

        def recording(path):
            seen.append(path)
            return SuiteExecutionResult(exit_code=0, stdout=f"\n  \nRan {path}\n\n", stderr="   \n ")

        tool = build_run_tests_tool(recording)
        # Upstream's rendering: leading blank lines and trailing whitespace go, empty stderr is left out.
        assert (await tool.call({"test_path": "tests/x.py"}, ctx)).content == (
            f"{exit_line(0)}\n{NOT_MEASURED}\n{STAYS_RED}\n\nRan tests/x.py")
        for args in ({"test_path": ""}, {"test_path": None}, {}):
            await tool.call(args, ctx)
            assert seen[-1] == "."

        fail = build_run_tests_tool(lambda p: SuiteExecutionResult(exit_code=1, stdout="F", stderr="E"))
        res = await fail.call({}, ctx)
        assert (res.is_error, res.exit_code) == (False, 1)
        assert res.content == f"{exit_line(1)}\n{NOT_MEASURED}\n{STAYS_RED}\n\nF\nE"

        quiet = build_run_tests_tool(lambda p: SuiteExecutionResult(exit_code=1, stderr="only stderr"))
        assert (await quiet.call({}, ctx)).content.endswith("\n\nonly stderr")

        two = build_run_tests_tool(lambda p, c: SuiteExecutionResult(exit_code=0, stdout=f"2 {p} {c is ctx}"))
        assert (await two.call({"test_path": "a"}, ctx)).content.endswith("2 a True")

        def unavailable(path):
            raise RunTestsUnavailable("runner exploded")

        res = await build_run_tests_tool(unavailable).call({}, ctx)
        assert (res.is_error, res.content) == (
            True, "RunTests could not run the tests: runner exploded\nTDD ledger: unchanged.")

    asyncio.run(go())


class _RecordingWorkspace:
    def __init__(self, exit_code=0, exc=None, report=None):
        self.calls = []
        self.exit_code, self.exc = exit_code, exc
        self.files: dict[str, str] = {}
        self.report = report
        self.deleted: list[str] = []

    def write_file(self, path, content):
        self.files[path] = content

    def read_file(self, path):
        if path == COVERAGE_REPORT_PATH and self.report is not None:
            return self.report
        raise FileNotFoundError(path)

    def delete_file(self, path):
        self.deleted.append(path)

    def execute(self, cmd, timeout=None, env=None):
        self.calls.append((cmd, timeout))
        if self.exc:
            raise self.exc
        from app.workspace.base import CommandResult
        return CommandResult(stdout="pytest output", stderr="", exit_code=self.exit_code, duration=0.1,
                             workspace="local")


def test_workspace_command_is_quoted_and_time_limited():
    ws = _RecordingWorkspace()
    ctx = tool_context_for(AppStateStore(), workspace=ws)
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests/a b.py; rm -rf /"}, ctx))
    assert res.exit_code == 0 and "pytest output" in res.content
    assert ws.calls == [(pytest_command("tests/a b.py; rm -rf /"), 120.0)]
    assert ws.files == {COVERAGE_CONFIG_PATH: COVERAGE_CONFIG}
    junit = ".tddagents/coverage/junit.xml"
    assert ws.deleted == [COVERAGE_REPORT_PATH, junit, COVERAGE_REPORT_PATH, junit]
    assert pytest_command("tests/a b.py; rm -rf /").startswith(
        "PYTHONPATH=. python -m pytest 'tests/a b.py; rm -rf /' -vv --tb=short -rs --rootdir=. ")
    assert pytest_command("tests/x.py") == (
        "PYTHONPATH=. python -m pytest tests/x.py -vv --tb=short -rs --rootdir=. "
        "--cov=. --cov-config=.tddagents/coverage/coveragerc --cov-report=term-missing "
        "--cov-report=json:.tddagents/coverage/report.json "
        "--junitxml=.tddagents/coverage/junit.xml -o junit_family=xunit1 "
        "$([ -f pytest.ini ] || [ -f .pytest.ini ] || grep -qs '^\\[tool\\.pytest' pyproject.toml "
        "|| grep -qs '^\\[pytest\\]' tox.ini || grep -qs '^\\[tool:pytest\\]' setup.cfg "
        "|| printf %s '-c /dev/null')")


POISONED_PARENT = '[tool.pytest.ini_options]\naddopts = "--no-such-flag"\n'


def _isolated_run(python_workspace: Any, tmp_path: Path, files: dict[str, str]) -> Any:
    # The workspace sits under a directory whose pyproject.toml would break any pytest run that
    # loads it, as TDDAgents' own does for `.tddagents/runs/<thread>/workspace`.
    (tmp_path / "pyproject.toml").write_text(POISONED_PARENT)
    for name, text in files.items():
        python_workspace.write_file(name, text)
    ctx = tool_context_for(AppStateStore(), workspace=python_workspace)
    return asyncio.run(build_run_tests_tool(None).call({"test_path": "tests"}, ctx))


def test_a_workspace_without_pytest_config_ignores_its_parents_config(python_workspace, tmp_path):
    res = _isolated_run(python_workspace, tmp_path, {"tests/test_a.py": "def test_a():\n    assert True\n"})
    assert res.exit_code == 0, res.content
    assert "rootdir: " + str(Path(python_workspace.root).resolve()) in res.content


@pytest.mark.parametrize("config_name, config_text", [
    ("pytest.ini", "[pytest]\npython_files = check_*.py\n"),
    (".pytest.ini", "[pytest]\npython_files = check_*.py\n"),
    ("pyproject.toml", "[tool.pytest.ini_options]\npython_files = ['check_*.py']\n"),
    ("tox.ini", "[pytest]\npython_files = check_*.py\n"),
    ("setup.cfg", "[tool:pytest]\npython_files = check_*.py\n"),
])
def test_the_workspaces_own_pytest_config_is_honoured(python_workspace, tmp_path, config_name, config_text):
    res = _isolated_run(python_workspace, tmp_path, {
        config_name: config_text, "tests/check_a.py": "def test_a():\n    assert False\n"})
    assert res.exit_code == 1, res.content
    assert "configfile: " + config_name in res.content


def test_timeout_follows_the_bash_default(monkeypatch):
    monkeypatch.setenv("BASH_DEFAULT_TIMEOUT_MS", "30000")
    assert run_tests_timeout_seconds() == 30.0
    monkeypatch.delenv("BASH_DEFAULT_TIMEOUT_MS")
    assert run_tests_timeout_seconds() == 120.0


@pytest.mark.parametrize("exc", [RuntimeError("Sandbox crashed"), WorkspaceTimeout("slow")])
def test_workspace_failure_is_unavailable_not_red(exc):
    """A crashed or timed-out run used to be mapped to exit 1 and could confirm RED."""
    ledger = PhaseLedger(phase=TddPhase.RED)
    store = AppStateStore(AppState(phase_ledger=ledger))
    ctx = tool_context_for(store, workspace=_RecordingWorkspace(exc=exc))
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests/t.py"}, ctx))
    assert res.is_error is True
    assert res.content == (f"RunTests could not run the tests: pytest could not be executed in the workspace: {exc}\n"
                           "TDD ledger: unchanged.")
    assert store.get().phase_ledger == ledger


def test_workspace_without_execute_is_unavailable():
    ctx = tool_context_for(AppStateStore(), workspace=object())
    res = asyncio.run(build_run_tests_tool(None).call({}, ctx))
    assert res.is_error is True and "no workspace is available" in res.content


# ── integration: real pytest in a real LocalWorkspace ────────────────────────

def test_real_pytest_red_green_and_a_measured_refactor_in_a_local_workspace(python_workspace):
    ws = python_workspace
    store = AppStateStore()
    ctx = tool_context_for(store, workspace=ws)
    tool = build_run_tests_tool(None)

    ws.write_file("tests/test_add.py", "from add import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    ws.write_file("add.py", "def add(a, b):\n    return 0\n")
    red = asyncio.run(tool.call({"test_path": "tests"}, ctx))
    assert red.exit_code == 1 and red.is_error is False
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, tests_written=True, green_passed=False)

    # Half of `sign` is never run: GREEN passes, and the uncovered lines are shown for REFACTOR.
    ws.write_file("add.py", "def add(a, b):\n    return a + b\n\n\ndef sign(x):\n    if x < 0:\n        return -1\n"
                            "    return 1\n")
    green = asyncio.run(tool.call({"test_path": "tests"}, ctx))
    assert green.exit_code == 0 and store.get().phase_ledger.phase == TddPhase.REFACTOR
    assert "add.py" in green.content and "Missing" in green.content  # term-missing for the model
    ws.write_file("tests/test_add.py", "from add import add, sign\n\ndef test_add():\n    assert add(2, 3) == 5\n\n"
                                       "def test_sign():\n    assert sign(-2) == -1\n    assert sign(2) == 1\n")
    done = asyncio.run(tool.call({"test_path": "tests"}, ctx))
    assert done.exit_code == 0, done.content
    assert store.get().phase_ledger.is_cycle_complete
    assert store.get().phase_ledger.coverage == 100.0
    assert not ws.exists(COVERAGE_REPORT_PATH)


def test_real_pytest_with_no_production_code_passes_refactor(python_workspace):
    ws = python_workspace
    store = AppStateStore(AppState(phase_ledger=REFACTORING))
    ws.write_file("tests/test_t.py", "def test_t():\n    assert True\n")
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests"}, tool_context_for(store, workspace=ws)))
    assert "Coverage: no production code to measure\nTDD ledger: REFACTOR passed" in res.content
    assert store.get().phase_ledger.is_cycle_complete


def test_real_pytest_with_no_tests_cannot_confirm_red(python_workspace):
    ws = python_workspace
    store = AppStateStore()
    ws.write_file("tests/helper.py", "X = 1\n")
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests"}, tool_context_for(store, workspace=ws)))
    assert res.exit_code == 5 and res.is_error is True
    assert "no tests were collected" in res.content
    assert store.get().phase_ledger == PhaseLedger()


# ── exit 2: collection errors pytest records are RED evidence ────────────────
#
# Read from pytest's JUnit report (`collection_errors`), never from the error text. The live run
# that motivated this: RunTests called `No module named 'fastapi'` "the code under test does not
# exist yet", and the model wrote a fastapi.py.

COLLECTION_FAILURE = SuiteExecutionResult(
    exit_code=2, stdout="E   ModuleNotFoundError: No module named 'fastapi'\n!!! Interrupted: 1 error !!!",
    collection_errors=frozenset({"tests/test_api.py"}))


def test_recorded_collection_errors_are_red_evidence_reported_without_a_cause():
    store = AppStateStore()
    res = asyncio.run(build_run_tests_tool(lambda p: COLLECTION_FAILURE).call({}, ws_ctx(store)))
    assert (res.is_error, res.exit_code) == (False, 2)
    assert res.content == (
        f"{exit_line(2)}\npytest reported errors collecting: tests/test_api.py\n{NOT_MEASURED}\n"
        f"TDD ledger: RED → GREEN. {RED_RULE}: they changed, and this run counts.\n\n"
        "E   ModuleNotFoundError: No module named 'fastapi'\n!!! Interrupted: 1 error !!!")
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, tests_written=True)


@pytest.mark.parametrize("phase", [TddPhase.GREEN, TddPhase.REFACTOR])
def test_recorded_collection_errors_are_a_non_pass_in_green_and_refactor(phase):
    ledger = PhaseLedger(phase=phase, tests_written=True, green_passed=phase == TddPhase.REFACTOR)
    store = AppStateStore(AppState(phase_ledger=ledger))
    res = asyncio.run(build_run_tests_tool(lambda p: COLLECTION_FAILURE).call({}, ws_ctx(store)))
    assert res.is_error is False
    assert f"TDD ledger: still {phase.value}. {PHASE_RULES[phase]}: this run had collection errors." in res.content
    assert store.get().phase_ledger == ledger


NO_CAUSE_PHRASES = ("does not exist yet", "not installed", "not written", "missing code", "install", "should",
                    "must", "implement")


@pytest.mark.parametrize("result", [
    COLLECTION_FAILURE,
    SuiteExecutionResult(exit_code=1, stdout="1 failed"),
    SuiteExecutionResult(exit_code=0, stdout="1 passed, 1 skipped", collected_files=frozenset()),
    SuiteExecutionResult(exit_code=5),
])
@pytest.mark.parametrize("phase", list(TddPhase))
def test_runtests_states_no_cause_and_gives_no_advice(result, phase):
    """Everything above the raw output is an observation or a stated rule (2026-10-28)."""
    ledger = PhaseLedger(phase=phase, tests_written=phase != TddPhase.RED, green_passed=phase == TddPhase.REFACTOR)
    store = AppStateStore(AppState(phase_ledger=ledger))
    res = asyncio.run(build_run_tests_tool(lambda p: result).call({}, ws_ctx(store)))
    head = res.content.split("\n\n", 1)[0]
    for phrase in NO_CAUSE_PHRASES:
        assert phrase not in head.lower(), (phrase, head)


def test_real_pytest_missing_module_confirms_red(python_workspace):
    ws = python_workspace
    store = AppStateStore()
    ws.write_file("tests/test_calc.py", "import calculator\n\ndef test_add():\n    assert calculator.add(2, 3) == 5\n")
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests"}, tool_context_for(store, workspace=ws)))
    assert res.exit_code == 2 and res.is_error is False
    assert "pytest reported errors collecting: tests/test_calc.py" in res.content
    assert store.get().phase_ledger.tests_written is True


def test_real_pytest_missing_package_is_reported_as_pytest_recorded_it(python_workspace):
    """The incident: the result names the file and shows pytest's own line, and says nothing about why."""
    ws = python_workspace
    store = AppStateStore()
    ws.write_file("tests/test_api.py", "import fastapi_not_installed\n\ndef test_a():\n    assert True\n")
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests"}, tool_context_for(store, workspace=ws)))
    head, output = res.content.split("\n\n", 1)
    assert head.splitlines()[:2] == [exit_line(2), "pytest reported errors collecting: tests/test_api.py"]
    assert "No module named 'fastapi_not_installed'" in output and "fastapi" not in head.split("\n", 2)[2]


def test_real_pytest_syntax_error_in_a_test_is_a_collection_error(python_workspace):
    """Accepted (user decision, 2026-10-28): any test file pytest cannot import is a collection error."""
    ws = python_workspace
    store = AppStateStore()
    ws.write_file("tests/test_bad.py", "def test_x(:\n    pass\n")
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests"}, tool_context_for(store, workspace=ws)))
    assert res.exit_code == 2 and res.is_error is False
    assert "pytest reported errors collecting: tests/test_bad.py" in res.content
    assert store.get().phase_ledger.tests_written is True


# ── Phase 5: the last observed run, kept for the TDD re-attach after compaction ─

def _record_run(result: SuiteExecutionResult, test_path: str = "tests/test_calc.py") -> AppStateStore:
    store = AppStateStore(AppState())
    ctx = ws_ctx(store)
    tool = build_run_tests_tool(lambda path: result)
    asyncio.run(tool.call({"test_path": test_path}, ctx))
    return store


def test_an_observed_run_is_recorded_beside_the_ledger():
    store = _record_run(SuiteExecutionResult(exit_code=1, stdout="E   assert 1 == 2\n1 failed", stderr="warn"))
    run = store.get().last_test_run
    assert run == TestRunRecord(test_path="tests/test_calc.py", exit_code=1, stdout="E   assert 1 == 2\n1 failed",
                                stderr="warn")
    assert store.get().phase_ledger.tests_written is True


def test_a_run_that_is_not_an_observation_is_not_recorded():
    store = _record_run(SuiteExecutionResult(exit_code=5, stdout="no tests ran"))
    assert store.get().last_test_run is None


def test_a_later_run_replaces_the_record():
    store = AppStateStore(AppState())
    ctx = tool_context_for(store)
    results = iter([SuiteExecutionResult(exit_code=1, stdout="red"), SuiteExecutionResult(exit_code=0, stdout="ok")])
    tool = build_run_tests_tool(lambda path: next(results))
    asyncio.run(tool.call({"test_path": "a"}, ctx))
    asyncio.run(tool.call({"test_path": "b"}, ctx))
    assert store.get().last_test_run == TestRunRecord(test_path="b", exit_code=0, stdout="ok", stderr="")


@pytest.mark.parametrize("report, output, reading", [
    ('{"totals": {"percent_covered": 92.5, "num_statements": 8}}', "", CoverageReading(CoverageStatus.MEASURED, 92.5)),
    (None, "WARNING: Failed to generate report: No data to report.", CoverageReading(CoverageStatus.NO_CODE)),
    (None, "pytest output", UNAVAILABLE),
])
def test_the_default_runner_reads_the_coverage_report_back(report, output, reading):
    from app.tdd.run_tests_tool.run_tests_tool import _default_test_runner

    ws = _RecordingWorkspace(report=report)
    ws.execute = lambda cmd, timeout=None, env=None: CommandResult(  # type: ignore[method-assign]
        stdout=output, stderr="", exit_code=0, duration=0.1, workspace="local")
    result = asyncio.run(_default_test_runner(".", tool_context_for(AppStateStore(), workspace=ws)))
    assert result.coverage == reading


@pytest.mark.parametrize("result, is_error", [
    (SuiteExecutionResult(exit_code=0, stdout="out"), False),
    (SuiteExecutionResult(exit_code=1, stdout="out"), False),
    (COLLECTION_FAILURE, False),
    (SuiteExecutionResult(exit_code=0, stdout="1 passed, 2 skipped"), False),
])
def test_a_background_run_reports_evidence_and_never_the_ledger(result, is_error):
    """`shareSetAppState: !isAsync`: a background worker's run makes no phase claim and writes nothing."""
    import dataclasses

    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    before = store.get()
    ctx = dataclasses.replace(ws_ctx(store), shares_app_state=False)
    res = asyncio.run(build_run_tests_tool(lambda path: result).call({}, ctx))
    head = res.content.split("\n\n", 1)[0].splitlines()
    assert head[0] == exit_line(result.exit_code) and head[-1] == BACKGROUND_LEDGER
    assert "GREEN" not in res.content and "REFACTOR" not in res.content
    assert (res.is_error, res.exit_code) == (is_error, result.exit_code)
    assert store.get() is before


def test_a_background_run_names_uncollected_files_and_skips():
    import dataclasses

    store = AppStateStore()
    ctx = dataclasses.replace(ws_ctx(store), shares_app_state=False)
    result = SuiteExecutionResult(exit_code=0, stdout="1 passed, 2 skipped", collected_files=frozenset())
    res = asyncio.run(build_run_tests_tool(lambda path: result).call({}, ctx))
    assert res.content.split("\n\n", 1)[0] == (
        f"{exit_line(0)}\nNot collected by pytest: tests/test_foo.py\nSkipped: 2\n{NOT_MEASURED}\n{BACKGROUND_LEDGER}")
    assert store.get().phase_ledger == PhaseLedger()


def test_a_background_run_that_is_not_evidence_is_an_error():
    import dataclasses

    ctx = dataclasses.replace(ws_ctx(AppStateStore()), shares_app_state=False)
    res = asyncio.run(build_run_tests_tool(lambda path: SuiteExecutionResult(exit_code=4)).call({}, ctx))
    assert res.is_error is True and NOT_EVIDENCE in res.content
