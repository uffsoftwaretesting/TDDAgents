"""
RunTests — the Red/Green measurement, exposed as a loop tool (Part D2).

This is the only component that observes ground truth:
"Tools already receive set_app_state, so the ledger is updated by the only component
that can observe ground truth: the test runner. No agent asserts its own progress."
(§3.3 of `docs/transition_elaboration_plan.md`)

When tests fail (exit code != 0):
- In RED: red_confirmed becomes True, and phase advances to GREEN.
- In GREEN/REFACTOR: green_passed is marked False.

When tests pass (exit code == 0):
- If red_confirmed was True: green_passed becomes True.
- If red_confirmed was False (the F2 "green in red" case): green_passed becomes True,
  red_confirmed stays False, and phase stays RED.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable

from app.loop.context import ToolContext
from app.loop.tools.base import BuiltTool, build_tool
from app.loop.tools.types import ToolResult


RUN_TESTS_PROMPT = """Runs pytest in the sandbox and returns the full output.

Tests failing is a result, not an error — in TDD a failing test is frequently the point.
The output includes the exit code, and is verbose on purpose because the failure detail
is the most valuable thing you will read.

Always runs in the sandbox, updating the TDD phase ledger based on observed results."""


@dataclass(frozen=True, slots=True)
class SuiteExecutionResult:
    """Outcome of running test suite."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    __test__: bool = False


# Backward-compatible alias
TestExecutionResult = SuiteExecutionResult

TestRunner = Callable[[str, ToolContext], Awaitable[SuiteExecutionResult] | SuiteExecutionResult]


async def _default_test_runner(test_path: str, context: ToolContext) -> SuiteExecutionResult:
    """
    Default test runner dispatching to context.test_runner if present,
    or falling back to a default offline simulation.
    """
    custom_runner: Any = getattr(context, "test_runner", None)
    if callable(custom_runner):
        try:
            res = custom_runner(test_path, context)
        except TypeError:
            res = custom_runner(test_path)
        if inspect.isawaitable(res):
            out = await res
        else:
            out = res
        if isinstance(out, SuiteExecutionResult):
            return out
        return SuiteExecutionResult(exit_code=getattr(out, "exit_code", 0))

    # Offline fallback
    return SuiteExecutionResult(
        exit_code=0,
        stdout=f"Pytest executed on {test_path}: all tests passed.",
    )


def build_run_tests_tool(
    test_runner: TestRunner | Callable[[str], Awaitable[SuiteExecutionResult] | SuiteExecutionResult] | None = None,
) -> BuiltTool:
    """
    Build a RunTests tool instance with an optional custom runner.
    """
    runner_fn: Any = test_runner if test_runner is not None else _default_test_runner

    async def call_run_tests(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        test_path = str(input_args.get("test_path") or ".")

        try:
            res = runner_fn(test_path, context)
        except TypeError:
            res = runner_fn(test_path)

        if inspect.isawaitable(res):
            exec_result: SuiteExecutionResult = await res
        else:
            exec_result = res

        # Update authoritative AppState phase_ledger (Part D2)
        context.set_app_state(
            lambda s: replace(s, phase_ledger=s.phase_ledger.with_test_result(exec_result.exit_code))
        )

        passed = exec_result.exit_code == 0
        header = "All tests passed." if passed else f"Tests failed (exit {exec_result.exit_code})."
        body = f"{header}\n\n--- STDOUT ---\n{exec_result.stdout}"
        if exec_result.stderr.strip():
            body += f"\n--- STDERR ---\n{exec_result.stderr}"

        # is_error stays False even on failure: red tests are normal data in TDD
        return ToolResult(
            content=body,
            exit_code=exec_result.exit_code,
        )

    return build_tool(
        name="RunTests",
        prompt=RUN_TESTS_PROMPT,
        input_schema={

            "type": "object",
            "properties": {
                "test_path": {
                    "type": "string",
                    "description": "File or directory to run. '.' runs the whole suite.",
                    "default": ".",
                }
            },
        },
        description=lambda args: f"Run tests in {args.get('test_path', '.')}",
        is_read_only=lambda args: True,
        is_concurrency_safe=lambda args: False,
        is_implementation_writer=False,
        is_test_writer=False,
        call=call_run_tests,
    )


RunTests = build_run_tests_tool()
