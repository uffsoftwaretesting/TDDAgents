"""
Tests for RunTests loop tool and phase ledger updates (Part D2).
"""

from __future__ import annotations

import asyncio

import pytest

from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.tools.run_tests import (
    LEDGER_EXIT_CODES,
    PYTEST_EXIT_REASONS,
    RunTests,
    RunTestsUnavailable,
    SuiteExecutionResult,
    build_run_tests_tool,
    pytest_command,
    run_tests_timeout_seconds,
)
from app.workspace.base import WorkspaceTimeout


def test_run_tests_in_red_failing_test_advances_to_green():
    async def go():
        store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
        ctx = tool_context_for(store)

        def failing_runner(path):
            return SuiteExecutionResult(exit_code=1, stdout="Failing test", stderr="Err")

        tool = build_run_tests_tool(failing_runner)

        res = await tool.call({"test_path": "tests/test_foo.py"}, ctx)

        assert res.is_error is False
        assert res.exit_code == 1
        assert "Tests failed (exit 1)" in res.content
        assert "Failing test" in res.content
        assert "Err" in res.content

        # Ground truth updated in AppState!
        current_ledger = store.get().phase_ledger
        assert current_ledger.phase == TddPhase.GREEN
        assert current_ledger.red_confirmed is True
        assert current_ledger.green_passed is False

    asyncio.run(go())


def test_run_tests_in_green_passing_test_marks_green_passed():
    async def go():
        store = AppStateStore(
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=False))
        )
        ctx = tool_context_for(store)

        def passing_runner(path):
            return SuiteExecutionResult(exit_code=0, stdout="All tests passed.")

        tool = build_run_tests_tool(passing_runner)

        res = await tool.call({}, ctx)

        assert res.is_error is False
        assert res.exit_code == 0
        assert "All tests passed." in res.content

        current_ledger = store.get().phase_ledger
        assert current_ledger.phase == TddPhase.GREEN
        assert current_ledger.red_confirmed is True
        assert current_ledger.green_passed is True
        assert current_ledger.is_cycle_complete is True

    asyncio.run(go())


def test_run_tests_green_in_red_f2_case():
    async def go():
        store = AppStateStore(
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED, red_confirmed=False, green_passed=False))
        )
        ctx = tool_context_for(store)

        def passing_runner(path):
            return SuiteExecutionResult(exit_code=0, stdout="1 passed.")

        tool = build_run_tests_tool(passing_runner)

        res = await tool.call({"test_path": "."}, ctx)

        assert res.is_error is False
        assert res.exit_code == 0

        current_ledger = store.get().phase_ledger
        assert current_ledger.phase == TddPhase.RED
        assert current_ledger.red_confirmed is False
        assert current_ledger.green_passed is True
        assert current_ledger.is_cycle_complete is False

    asyncio.run(go())


def test_exit_2_is_not_an_observation_and_leaves_the_ledger():
    async def go():
        ledger = PhaseLedger(phase=TddPhase.RED, red_confirmed=False, green_passed=False)
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
    ledger = PhaseLedger(phase=phase, red_confirmed=phase != TddPhase.RED,
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
    from app.loop.tools.run_tests import RUN_TESTS_PROMPT

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


def test_suite_execution_result_defaults_and_alias():
    from app.loop.tools.run_tests import TestExecutionResult

    res = SuiteExecutionResult(exit_code=0)
    assert (res.exit_code, res.stdout, res.stderr, res.__test__) == (0, "", "", False)
    assert TestExecutionResult is SuiteExecutionResult


class _Wrap:
    def __init__(self, ctx):
        self._ctx = ctx

    def __getattr__(self, item):
        return getattr(self._ctx, item)


def test_default_test_runner_context_runner_shapes():
    async def go():
        from app.loop.tools.run_tests import _default_test_runner

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
    from app.loop.tools.run_tests import _default_test_runner

    class NoCode(_Wrap):
        def test_runner(self, path):
            return type("R", (), {"exit_code": bad_exit})()

    with pytest.raises(RunTestsUnavailable, match="returned no exit code"):
        asyncio.run(_default_test_runner("t.py", NoCode(tool_context_for(AppStateStore()))))  # type: ignore[arg-type]


def test_non_callable_context_runner_falls_through_to_workspace_rule():
    from app.loop.tools.run_tests import _default_test_runner

    class NotCallable(_Wrap):
        test_runner = "nope"

    with pytest.raises(RunTestsUnavailable, match="no workspace"):
        asyncio.run(_default_test_runner("t.py", NotCallable(tool_context_for(AppStateStore()))))  # type: ignore


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
            "All tests passed.\n\n--- STDOUT ---\nRan tests/x.py")
        for args in ({"test_path": ""}, {"test_path": None}, {}):
            await tool.call(args, ctx)
            assert seen[-1] == "."

        fail = build_run_tests_tool(lambda p: SuiteExecutionResult(exit_code=1, stdout="F", stderr="E"))
        res = await fail.call({}, ctx)
        assert (res.is_error, res.exit_code) == (False, 1)
        assert res.content == "Tests failed (exit 1).\n\n--- STDOUT ---\nF\n--- STDERR ---\nE"

        two = build_run_tests_tool(lambda p, c: SuiteExecutionResult(exit_code=0, stdout=f"2 {p} {c is ctx}"))
        assert (await two.call({"test_path": "a"}, ctx)).content.endswith("2 a True")

        def unavailable(path):
            raise RunTestsUnavailable("runner exploded")

        res = await build_run_tests_tool(unavailable).call({}, ctx)
        assert (res.is_error, res.content) == (
            True, "RunTests could not run the tests: runner exploded. The TDD ledger was not changed.")

    asyncio.run(go())


class _RecordingWorkspace:
    def __init__(self, exit_code=0, exc=None):
        self.calls = []
        self.exit_code, self.exc = exit_code, exc

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
    assert ws.calls == [("PYTHONPATH=. python -m pytest 'tests/a b.py; rm -rf /' -vv --tb=short", 120.0)]
    assert pytest_command("tests/x.py") == "PYTHONPATH=. python -m pytest tests/x.py -vv --tb=short"


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

def test_real_pytest_red_then_green_in_a_local_workspace(python_workspace):
    ws = python_workspace
    store = AppStateStore()
    ctx = tool_context_for(store, workspace=ws)
    tool = build_run_tests_tool(None)

    ws.write_file("tests/test_add.py", "from add import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    ws.write_file("add.py", "def add(a, b):\n    return 0\n")
    red = asyncio.run(tool.call({"test_path": "tests"}, ctx))
    assert red.exit_code == 1 and red.is_error is False
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True, green_passed=False)

    ws.write_file("add.py", "def add(a, b):\n    return a + b\n")
    green = asyncio.run(tool.call({"test_path": "tests"}, ctx))
    assert green.exit_code == 0
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


@pytest.mark.parametrize("error_line", [
    "E   ModuleNotFoundError: No module named 'calculator'",
    "E   ImportError: cannot import name 'sub' from 'calc2' (/w/calc2.py)",
    "E   AttributeError: module 'calc2' has no attribute 'nothing'",
])
def test_missing_code_collection_errors_are_failing_tests(error_line):
    from app.loop.tools.run_tests import is_missing_code_collection_error, ledger_exit_code

    res = SuiteExecutionResult(exit_code=2, stdout=BANNER + error_line + "\n!!! Interrupted: 1 error !!!")
    assert is_missing_code_collection_error(res) is True
    assert ledger_exit_code(res) == 1
    store = AppStateStore()
    out = asyncio.run(build_run_tests_tool(lambda p: res).call({}, tool_context_for(store)))
    assert out.is_error is False and out.exit_code == 2
    assert out.content.startswith(
        "Tests failed (exit 2: collection error — the code under test does not exist yet).")
    assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)


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
    from app.loop.tools.run_tests import is_missing_code_collection_error, ledger_exit_code

    res = SuiteExecutionResult(exit_code=2, stdout=stdout, stderr=stderr)
    expected = "calculator" in stderr
    assert is_missing_code_collection_error(res) is expected
    assert ledger_exit_code(res) == (1 if expected else None)


def test_collection_shape_only_applies_to_exit_2():
    from app.loop.tools.run_tests import is_missing_code_collection_error, ledger_exit_code

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
    assert store.get().phase_ledger.red_confirmed is True


def test_real_pytest_syntax_error_in_test_is_not_red(python_workspace):
    ws = python_workspace
    store = AppStateStore()
    ws.write_file("tests/test_bad.py", "def test_x(:\n    pass\n")
    res = asyncio.run(build_run_tests_tool(None).call({"test_path": "tests"}, tool_context_for(store, workspace=ws)))
    assert res.exit_code == 2 and res.is_error is True
    assert store.get().phase_ledger == PhaseLedger()
