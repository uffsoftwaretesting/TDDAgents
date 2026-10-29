"""
Tests for RunTests loop tool and phase ledger updates (Part D2).
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
    BACKGROUND_RUN_NOTE,
    LEDGER_EXIT_CODES,
    PYTEST_EXIT_REASONS,
    RunTests,
    RunTestsUnavailable,
    SuiteExecutionResult,
    build_run_tests_tool,
    pytest_command,
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


@pytest.mark.parametrize("exit_code, header", [(1, "Tests failed (exit 1)."), (0, "All tests passed.")])
def test_a_run_of_new_tests_ends_red_whatever_its_outcome(exit_code, header):
    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
    tool = build_run_tests_tool(lambda path: SuiteExecutionResult(exit_code=exit_code, stdout="out", stderr="Err"))
    res = asyncio.run(tool.call({"test_path": "tests/test_foo.py"}, ws_ctx(store)))
    assert (res.is_error, res.exit_code) == (False, exit_code)
    assert res.content == (
        f"{header}\nRED is done: this sub-requirement's tests were written and run. The phase is GREEN: "
        "implement the code they describe and run RunTests until every test passes.\n\n--- STDOUT ---\nout"
        "\n--- STDERR ---\nErr")
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, tests_written=True)


def test_unchanged_tests_keep_red():
    files = dict(TESTED)
    baseline = fingerprint_test_files(FakeWorkspace(files=files))
    store = AppStateStore(AppState(tdd_item_test_baseline=baseline))
    tool = build_run_tests_tool(lambda path: SuiteExecutionResult(exit_code=1, stdout="out"))
    res = asyncio.run(tool.call({}, ws_ctx(store, files)))
    assert res.content.startswith(
        "Tests failed (exit 1).\nNo test file changed since this sub-requirement started, so the phase stays RED: "
        "write or extend its tests (robust, covering every relevant scenario and edge case), then run RunTests.")
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
    assert res.content.startswith(
        "All tests passed.\nGREEN is done. The phase is REFACTOR, which is mandatory: improve the code and the tests "
        "and raise coverage (coverage 70.0% (rule of thumb: 90%)), then run RunTests until every test passes.")
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True,
                                                   coverage=70.0)


def test_a_failure_in_green_stays_in_green():
    ledger = PhaseLedger(phase=TddPhase.GREEN, tests_written=True)
    store = AppStateStore(AppState(phase_ledger=ledger))
    res = asyncio.run(build_run_tests_tool(lambda path: SuiteExecutionResult(exit_code=1)).call({}, ws_ctx(store)))
    assert res.content.startswith("Tests failed (exit 1).\nThe phase stays GREEN: every test must pass.")
    assert store.get().phase_ledger == ledger


REFACTORING = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True)


@pytest.mark.parametrize("result, status, passed", [
    (SuiteExecutionResult(exit_code=0, coverage=MET),
     "REFACTOR is done (coverage 95.0% (rule of thumb: 90%)): this sub-requirement's cycle is complete.", True),
    # Coverage is a rule of thumb, not a gate (user decision, 2026-10-28).
    (SuiteExecutionResult(exit_code=0, coverage=UNDER),
     "REFACTOR is done (coverage 70.0% (rule of thumb: 90%)): this sub-requirement's cycle is complete.", True),
    (SuiteExecutionResult(exit_code=0),
     "REFACTOR is done (coverage could not be measured): this sub-requirement's cycle is complete.", True),
    (SuiteExecutionResult(exit_code=1, coverage=MET), "REFACTOR is not done: every test must pass.", False),
])
def test_refactor_is_judged_by_a_passing_run(result, status, passed):
    store = AppStateStore(AppState(phase_ledger=REFACTORING))
    res = asyncio.run(build_run_tests_tool(lambda path: result).call({}, ws_ctx(store)))
    assert res.content.splitlines()[1] == status
    assert store.get().phase_ledger.refactor_passed is passed
    assert store.get().phase_ledger.is_cycle_complete is passed


def test_exit_2_is_not_an_observation_and_leaves_the_ledger():
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
        assert res.content == (
            "Tests could not be evaluated (exit 2: test execution was interrupted). The TDD ledger was not "
            "changed.\n\n--- STDOUT ---\nSyntax error\n--- STDERR ---\nFailed")
        assert store.get().phase_ledger == ledger

    asyncio.run(go())


@pytest.mark.parametrize("code, reason", [
    (2, "test execution was interrupted"),
    (3, "pytest hit an internal error"),
    (4, "pytest was invoked incorrectly (usage error)"),
    (5, "no tests were collected"),
    (127, "pytest did not report a test outcome"),
    (-9, "pytest did not report a test outcome"),
])
@pytest.mark.parametrize("phase", list(TddPhase))
def test_non_observation_codes_never_move_the_ledger(code, reason, phase):
    """'No tests collected' can never confirm RED; nothing but 0/1 is evidence."""
    ledger = PhaseLedger(phase=phase, tests_written=phase != TddPhase.RED,
                         green_passed=phase == TddPhase.REFACTOR)
    store = AppStateStore(AppState(phase_ledger=ledger))
    tool = build_run_tests_tool(lambda path: SuiteExecutionResult(exit_code=code, stdout="o"))
    res = asyncio.run(tool.call({}, tool_context_for(store)))
    assert res.is_error is True
    assert res.exit_code == code
    assert res.content.startswith(f"Tests could not be evaluated (exit {code}: {reason}). The TDD ledger")
    assert store.get().phase_ledger == ledger


def test_ledger_codes_constant():
    assert LEDGER_EXIT_CODES == frozenset({0, 1})
    assert PYTEST_EXIT_REASONS == {
        2: "test execution was interrupted",
        3: "pytest hit an internal error",
        4: "pytest was invoked incorrectly (usage error)",
        5: "no tests were collected",
    }


def test_default_instance_and_properties():
    from app.tdd.run_tests_tool.run_tests_tool import PROMPT_PATH
    from app.utils.frontmatter_parser import parse_markdown_frontmatter

    RUN_TESTS_PROMPT = parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1]
    assert RUN_TESTS_PROMPT.startswith("Runs pytest in the workspace and returns the full output.")
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


def test_fails_closed_without_a_workspace():
    """The old offline fallback reported 'all tests passed' with nothing run."""
    ledger = PhaseLedger(phase=TddPhase.RED)
    store = AppStateStore(AppState(phase_ledger=ledger))
    res = asyncio.run(RunTests.call({}, tool_context_for(store)))
    assert res.is_error is True
    assert res.exit_code is None
    assert res.content == ("RunTests could not run the tests: no workspace is available to run the tests in. "
                           "The TDD ledger was not changed.")
    assert store.get().phase_ledger == ledger


def test_suite_execution_result_defaults():
    res = SuiteExecutionResult(exit_code=0)
    assert (res.exit_code, res.stdout, res.stderr, res.coverage, res.__test__) == (0, "", "", UNAVAILABLE, False)


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


STAYS_RED = ("No test file changed since this sub-requirement started, so the phase stays RED: write or extend "
             "its tests (robust, covering every relevant scenario and edge case), then run RunTests.")


def test_call_argument_handling_and_formatting():
    async def go():
        store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
        ctx = tool_context_for(store)
        seen: list[str] = []

        def recording(path):
            seen.append(path)
            return SuiteExecutionResult(exit_code=0, stdout=f"Ran {path}", stderr="   \n ")

        tool = build_run_tests_tool(recording)
        assert (await tool.call({"test_path": "tests/x.py"}, ctx)).content == (
            f"All tests passed.\n{STAYS_RED}\n\n--- STDOUT ---\nRan tests/x.py")
        for args in ({"test_path": ""}, {"test_path": None}, {}):
            await tool.call(args, ctx)
            assert seen[-1] == "."

        fail = build_run_tests_tool(lambda p: SuiteExecutionResult(exit_code=1, stdout="F", stderr="E"))
        res = await fail.call({}, ctx)
        assert (res.is_error, res.exit_code) == (False, 1)
        assert res.content == f"Tests failed (exit 1).\n{STAYS_RED}\n\n--- STDOUT ---\nF\n--- STDERR ---\nE"

        two = build_run_tests_tool(lambda p, c: SuiteExecutionResult(exit_code=0, stdout=f"2 {p} {c is ctx}"))
        assert (await two.call({"test_path": "a"}, ctx)).content.endswith("2 a True")

        def unavailable(path):
            raise RunTestsUnavailable("runner exploded")

        res = await build_run_tests_tool(unavailable).call({}, ctx)
        assert (res.is_error, res.content) == (
            True, "RunTests could not run the tests: runner exploded. The TDD ledger was not changed.")

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
    assert res.content == (f"RunTests could not run the tests: pytest could not be executed in the workspace: {exc}. "
                           "The TDD ledger was not changed.")
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
    assert "REFACTOR is done (coverage: no production code to measure)" in res.content
    assert store.get().phase_ledger.is_cycle_complete


def test_real_pytest_with_no_tests_cannot_confirm_red(python_workspace):
    ws = python_workspace
    store = AppStateStore()
    ws.write_file("tests/helper.py", "X = 1\n")
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests"}, tool_context_for(store, workspace=ws)))
    assert res.exit_code == 5 and res.is_error is True
    assert "no tests were collected" in res.content
    assert store.get().phase_ledger == PhaseLedger()


# ── exit 2: a collection error from missing code is RED evidence ─────────────

BANNER = "______ ERROR collecting tests/test_mod.py ______\n"


@pytest.mark.parametrize("error_line, header", [
    ("E   ModuleNotFoundError: No module named 'calculator'",
     "Tests failed (exit 2: collection error — pytest could not import 'calculator')."),
    ("E   ImportError: cannot import name 'sub' from 'calc2' (/w/calc2.py)",
     "Tests failed (exit 2: collection error — an import failed, see the error below)."),
    ("E   AttributeError: module 'calc2' has no attribute 'nothing'",
     "Tests failed (exit 2: collection error — an import failed, see the error below)."),
])
def test_missing_code_collection_errors_are_failing_tests(error_line, header):
    from app.tdd.run_tests_tool.run_tests_tool import (
        is_missing_code_collection_error,
        ledger_exit_code,
    )

    res = SuiteExecutionResult(exit_code=2, stdout=BANNER + error_line + "\n!!! Interrupted: 1 error !!!")
    assert is_missing_code_collection_error(res) is True
    assert ledger_exit_code(res) == 1
    store = AppStateStore()
    out = asyncio.run(build_run_tests_tool(lambda p: res).call({}, ws_ctx(store)))
    assert out.is_error is False and out.exit_code == 2
    # Said as observed: "the code under test does not exist yet" once led a model to fake a package.
    assert out.content.startswith(header)
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, tests_written=True)


@pytest.mark.parametrize("stdout, stderr", [
    (BANNER + "E   SyntaxError: invalid syntax", ""),                                  # broken test
    (BANNER + "E   ModuleNotFoundError: x\nE   SyntaxError: bad", ""),                # any syntax error wins
    (BANNER + "E   IndentationError: unexpected indent", ""),
    (BANNER + "E   TabError: inconsistent", ""),
    (BANNER + "E   NameError: name 'x' is not defined", ""),                          # not a missing-code kind
    ("E   ModuleNotFoundError: No module named 'x'", ""),                              # no collection banner
    ("KeyboardInterrupt", ""),                                                         # interrupted
    ("", BANNER + "E   ModuleNotFoundError: No module named 'calculator'"),           # stderr counts too
])
def test_other_exit_2_runs_are_not_evidence(stdout, stderr):
    from app.tdd.run_tests_tool.run_tests_tool import (
        is_missing_code_collection_error,
        ledger_exit_code,
    )

    res = SuiteExecutionResult(exit_code=2, stdout=stdout, stderr=stderr)
    expected = "calculator" in stderr
    assert is_missing_code_collection_error(res) is expected
    assert ledger_exit_code(res) == (1 if expected else None)


def test_collection_shape_only_applies_to_exit_2():
    from app.tdd.run_tests_tool.run_tests_tool import (
        is_missing_code_collection_error,
        ledger_exit_code,
    )

    text = BANNER + "E   ModuleNotFoundError: No module named 'calculator'"
    for code in (1, 3, 4, 5, 0):
        assert is_missing_code_collection_error(SuiteExecutionResult(exit_code=code, stdout=text)) is False
    assert ledger_exit_code(SuiteExecutionResult(exit_code=5, stdout=text)) is None
    assert ledger_exit_code(SuiteExecutionResult(exit_code=0)) == 0
    assert ledger_exit_code(SuiteExecutionResult(exit_code=1)) == 1


def test_real_pytest_missing_module_confirms_red(python_workspace):
    ws = python_workspace
    store = AppStateStore()
    ws.write_file("tests/test_calc.py", "import calculator\n\ndef test_add():\n    assert calculator.add(2, 3) == 5\n")
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests"}, tool_context_for(store, workspace=ws)))
    assert res.exit_code == 2 and res.is_error is False
    assert store.get().phase_ledger.tests_written is True


def test_real_pytest_syntax_error_in_test_is_not_red(python_workspace):
    ws = python_workspace
    store = AppStateStore()
    ws.write_file("tests/test_bad.py", "def test_x(:\n    pass\n")
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests"}, tool_context_for(store, workspace=ws)))
    assert res.exit_code == 2 and res.is_error is True
    assert store.get().phase_ledger == PhaseLedger()


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


@pytest.mark.parametrize("result, header", [
    (SuiteExecutionResult(exit_code=0, stdout="out"), "All tests passed."),
    (SuiteExecutionResult(exit_code=1, stdout="out"), "Tests failed (exit 1)."),
    (SuiteExecutionResult(
        exit_code=2, stdout=BANNER + "E   ModuleNotFoundError: No module named 'x'\n!!! Interrupted: 1 error !!!"),
     "Tests failed (exit 2: collection error — pytest could not import 'x')."),
    (SuiteExecutionResult(exit_code=0, stdout="1 passed, 2 skipped"),
     "Tests ran without failures, but 2 test(s) were skipped"),
])
def test_a_background_run_reports_evidence_and_never_the_ledger(result, header):
    """`shareSetAppState: !isAsync`: a background worker's run makes no phase claim and writes nothing."""
    import dataclasses

    store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, tests_written=True)))
    before = store.get()
    ctx = dataclasses.replace(ws_ctx(store), shares_app_state=False)
    res = asyncio.run(build_run_tests_tool(lambda path: result).call({}, ctx))
    assert res.content.startswith(header)
    assert f"\n{BACKGROUND_RUN_NOTE}\n\n--- STDOUT ---\n" in res.content
    assert "GREEN" not in res.content and "REFACTOR" not in res.content
    assert (res.is_error, res.exit_code) == (False, result.exit_code)
    assert store.get() is before


def _returning(result: SuiteExecutionResult) -> Any:
    return lambda path: result


def test_a_background_run_names_uncollected_files():
    import dataclasses

    store = AppStateStore()
    ctx = dataclasses.replace(ws_ctx(store), shares_app_state=False)
    for code, prefix in ((0, "Tests ran without failures, but "), (1, "Tests failed (exit 1).\n")):
        result = SuiteExecutionResult(exit_code=code, collected_files=frozenset())
        res = asyncio.run(build_run_tests_tool(_returning(result)).call({}, ctx))
        assert res.content.startswith(prefix)
        assert "tests/test_foo.py" in res.content.split(BACKGROUND_RUN_NOTE)[0]
    assert store.get().phase_ledger == PhaseLedger()
