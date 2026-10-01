"""
Tests for RunTests loop tool and phase ledger updates (Part D2).
"""

from __future__ import annotations

import asyncio

from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.tools.run_tests import RunTests, SuiteExecutionResult, build_run_tests_tool


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


def test_run_tests_async_runner_and_context_injection():
    async def go():
        store = AppStateStore(
            AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED, red_confirmed=False, green_passed=False))
        )
        ctx = tool_context_for(store)

        async def async_runner(path, context):
            assert context is ctx
            return SuiteExecutionResult(exit_code=2, stdout="Syntax error", stderr="Failed")

        tool = build_run_tests_tool(async_runner)
        res = await tool.call({"test_path": "tests/test_bar.py"}, ctx)

        assert res.exit_code == 2
        assert "Syntax error" in res.content
        assert store.get().phase_ledger.phase == TddPhase.GREEN
        assert store.get().phase_ledger.red_confirmed is True

    asyncio.run(go())


def test_default_instance_and_properties():
    async def go():
        store = AppStateStore()
        ctx = tool_context_for(store)

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
            props = t.input_schema["properties"]["test_path"]
            assert props["type"] == "string"
            assert props["description"] == "File or directory to run. '.' runs the whole suite."
            assert props["default"] == "."
            assert t.is_read_only({}) is True
            assert t.is_read_only({"test_path": "foo"}) is True
            assert t.is_concurrency_safe({}) is False
            assert t.is_concurrency_safe({"test_path": "foo"}) is False
            assert t.is_implementation_writer() is False
            assert t.is_test_writer() is False
            assert t.description({"test_path": "tests/test_x.py"}) == "Run tests in tests/test_x.py"
            assert t.description({}) == "Run tests in ."
            assert t.description({"test_path": "XX.XX"}) == "Run tests in XX.XX"
            assert t.description({"other": "foo"}) == "Run tests in ."

        res = await RunTests.call({}, ctx)
        assert res.is_error is False
        assert res.exit_code == 0
        assert res.content == "All tests passed.\n\n--- STDOUT ---\nPytest executed on .: all tests passed."

    asyncio.run(go())


def test_suite_execution_result_defaults_and_alias():

    from app.loop.tools.run_tests import TestExecutionResult

    res = SuiteExecutionResult(exit_code=0)
    assert res.exit_code == 0
    assert res.stdout == ""
    assert res.stderr == ""
    assert res.__test__ is False
    assert TestExecutionResult is SuiteExecutionResult


def test_default_test_runner_branches():
    async def go():
        from app.loop.tools.run_tests import _default_test_runner

        store = AppStateStore()
        base_ctx = tool_context_for(store)

        # 1. 2-arg sync runner on context
        class ContextWithSyncRunner2Args:
            def __init__(self, ctx):
                self._ctx = ctx

            def test_runner(self, path, c):
                assert path == "tests/test_sync2.py"
                assert c is self
                return SuiteExecutionResult(exit_code=0, stdout="sync2 out", stderr="sync2 err")

            def __getattr__(self, item):
                return getattr(self._ctx, item)

        ctx_s2 = ContextWithSyncRunner2Args(base_ctx)
        res_s2 = await _default_test_runner("tests/test_sync2.py", ctx_s2)  # type: ignore[arg-type]
        assert res_s2.exit_code == 0
        assert res_s2.stdout == "sync2 out"
        assert res_s2.stderr == "sync2 err"

        # 2. 1-arg sync runner on context (TypeError fallback)
        class ContextWithSyncRunner1Arg:
            def __init__(self, ctx):
                self._ctx = ctx

            def test_runner(self, path):
                assert path == "tests/test_sync1.py"
                return SuiteExecutionResult(exit_code=1, stdout="sync1 out")

            def __getattr__(self, item):
                return getattr(self._ctx, item)

        ctx_s1 = ContextWithSyncRunner1Arg(base_ctx)
        res_s1 = await _default_test_runner("tests/test_sync1.py", ctx_s1)  # type: ignore[arg-type]
        assert res_s1.exit_code == 1
        assert res_s1.stdout == "sync1 out"

        # 3. 2-arg async runner on context
        class ContextWithAsyncRunner2Args:
            def __init__(self, ctx):
                self._ctx = ctx

            async def test_runner(self, path, c):
                assert path == "tests/test_async2.py"
                assert c is self
                return SuiteExecutionResult(exit_code=2, stdout="async2 out")

            def __getattr__(self, item):
                return getattr(self._ctx, item)

        ctx_a2 = ContextWithAsyncRunner2Args(base_ctx)
        res_a2 = await _default_test_runner("tests/test_async2.py", ctx_a2)  # type: ignore[arg-type]
        assert res_a2.exit_code == 2
        assert res_a2.stdout == "async2 out"

        # 4. 1-arg async runner on context (TypeError fallback)
        class ContextWithAsyncRunner1Arg:
            def __init__(self, ctx):
                self._ctx = ctx

            async def test_runner(self, path):
                assert path == "tests/test_async1.py"
                return SuiteExecutionResult(exit_code=3, stdout="async1 out")

            def __getattr__(self, item):
                return getattr(self._ctx, item)

        ctx_a1 = ContextWithAsyncRunner1Arg(base_ctx)
        res_a1 = await _default_test_runner("tests/test_async1.py", ctx_a1)  # type: ignore[arg-type]
        assert res_a1.exit_code == 3
        assert res_a1.stdout == "async1 out"

        # 5. Runner returning arbitrary object with exit_code
        class CustomResult:
            exit_code = 42

        class ContextWithCustomResult:
            def __init__(self, ctx):
                self._ctx = ctx

            def test_runner(self, path):
                return CustomResult()

            def __getattr__(self, item):
                return getattr(self._ctx, item)

        ctx_cr = ContextWithCustomResult(base_ctx)
        res_cr = await _default_test_runner("tests/test_custom.py", ctx_cr)  # type: ignore[arg-type]
        assert res_cr.exit_code == 42
        assert res_cr.stdout == ""
        assert res_cr.stderr == ""

        # 6. Runner returning arbitrary object without exit_code
        class ContextWithNoExitCode:
            def __init__(self, ctx):
                self._ctx = ctx

            def test_runner(self, path):
                return object()

            def __getattr__(self, item):
                return getattr(self._ctx, item)

        ctx_no_ec = ContextWithNoExitCode(base_ctx)
        res_no_ec = await _default_test_runner("tests/test_none.py", ctx_no_ec)  # type: ignore[arg-type]
        assert res_no_ec.exit_code == 0

        # 7. Non-callable test_runner attribute
        class ContextWithNonCallable:
            def __init__(self, ctx):
                self._ctx = ctx
                self.test_runner = "not-callable"

            def __getattr__(self, item):
                return getattr(self._ctx, item)

        ctx_nc = ContextWithNonCallable(base_ctx)
        res_nc = await _default_test_runner("tests/test_fallback.py", ctx_nc)  # type: ignore[arg-type]
        assert res_nc.exit_code == 0
        assert res_nc.stdout == "Pytest executed on tests/test_fallback.py: all tests passed."
        assert res_nc.stderr == ""

        # 8. Plain ToolContext without test_runner (offline fallback)
        res_offline = await _default_test_runner("tests/test_off.py", base_ctx)
        assert res_offline.exit_code == 0
        assert res_offline.stdout == "Pytest executed on tests/test_off.py: all tests passed."
        assert res_offline.stderr == ""

    asyncio.run(go())


def test_call_run_tests_argument_handling_and_formatting():
    async def go():
        store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
        ctx = tool_context_for(store)

        seen_paths: list[str] = []

        def recording_runner(path):
            seen_paths.append(path)
            return SuiteExecutionResult(exit_code=0, stdout=f"Ran {path}", stderr="   \n ")

        tool = build_run_tests_tool(recording_runner)

        # Explicit test_path
        res1 = await tool.call({"test_path": "tests/test_specific.py"}, ctx)
        assert seen_paths[-1] == "tests/test_specific.py"
        assert res1.is_error is False
        assert res1.exit_code == 0
        # When stderr is only whitespace, stderr section is not added
        assert res1.content == "All tests passed.\n\n--- STDOUT ---\nRan tests/test_specific.py"

        # Empty string test_path defaults to "."
        res2 = await tool.call({"test_path": ""}, ctx)
        assert seen_paths[-1] == "."
        assert res2.content == "All tests passed.\n\n--- STDOUT ---\nRan ."

        # None test_path defaults to "."
        res3 = await tool.call({"test_path": None}, ctx)
        assert seen_paths[-1] == "."
        assert res3.content == "All tests passed.\n\n--- STDOUT ---\nRan ."

        # Missing test_path defaults to "."
        res4 = await tool.call({}, ctx)
        assert seen_paths[-1] == "."
        assert res4.content == "All tests passed.\n\n--- STDOUT ---\nRan ."

        # Failure formatting with non-empty stderr
        def failing_runner(path):
            return SuiteExecutionResult(exit_code=5, stdout="Failed out", stderr="Error log")

        fail_tool = build_run_tests_tool(failing_runner)
        res5 = await fail_tool.call({"test_path": "tests/test_fail.py"}, ctx)
        assert res5.is_error is False
        assert res5.exit_code == 5
        assert (
            res5.content
            == "Tests failed (exit 5).\n\n--- STDOUT ---\nFailed out\n--- STDERR ---\nError log"
        )

        # Runner using 2-arg sync callable
        def sync_2arg_runner(path, c):
            assert c is ctx
            return SuiteExecutionResult(exit_code=0, stdout=f"2arg sync {path}")

        tool_sync2 = build_run_tests_tool(sync_2arg_runner)
        res6 = await tool_sync2.call({"test_path": "tests/test_sync2.py"}, ctx)
        assert res6.content == "All tests passed.\n\n--- STDOUT ---\n2arg sync tests/test_sync2.py"

        # Runner with test_runner=None uses _default_test_runner
        default_built_tool = build_run_tests_tool(None)
        res7 = await default_built_tool.call({"test_path": "tests/test_def.py"}, ctx)
        assert (
            res7.content
            == "All tests passed.\n\n--- STDOUT ---\nPytest executed on tests/test_def.py: all tests passed."
        )

    asyncio.run(go())
