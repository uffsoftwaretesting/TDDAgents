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

Only pytest's exit codes 0 (passed) and 1 (tests failed) are observations about the code
under test — plus one form of exit 2: a collection error raised because the code under test
does not exist yet (`ModuleNotFoundError`, `ImportError`, `AttributeError` while importing a
test), which is the classic first RED and counts as failing tests. Every other outcome —
2 interrupted, 3 internal error, 4 usage error, 5 no tests collected, a timeout, a missing
workspace, a crashed runner — says nothing about Red or Green, so it is reported as an
error and the ledger is left untouched. In particular "no tests collected" can never confirm
RED. The tool fails closed: with no workspace and no injected runner it refuses rather than
pretending the suite passed.
"""

from __future__ import annotations

import inspect
import re
import shlex
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Mapping

from app.loop.context import ToolContext
from app.loop.tools.base import BuiltTool, build_tool
from app.loop.tools.types import ToolResult


RUN_TESTS_PROMPT = """Runs pytest in the workspace and returns the full output.

Tests failing is a result, not an error — in TDD a failing test is frequently the point.
The output includes the exit code, and is verbose on purpose because the failure detail
is the most valuable thing you will read.

Updates the TDD phase ledger from the observed exit code: only a pass (0) or failing tests (1)
change it; anything else (no tests collected, usage error, timeout) is reported as an error."""


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


#: pytest exit codes that are observations about the code under test.
LEDGER_EXIT_CODES: frozenset[int] = frozenset({0, 1})

#: Why a non-observation exit code tells us nothing about Red or Green.
PYTEST_EXIT_REASONS: dict[int, str] = {
    2: "test execution was interrupted",
    3: "pytest hit an internal error",
    4: "pytest was invoked incorrectly (usage error)",
    5: "no tests were collected",
}


#: pytest's collection-error banner, and the exception kinds that mean "code not written yet".
_COLLECTION_BANNER = "ERROR collecting"
_MISSING_CODE_ERROR = re.compile(r"^E\s+(ModuleNotFoundError|ImportError|AttributeError):", re.MULTILINE)
_BROKEN_TEST_ERROR = re.compile(r"^E\s+(SyntaxError|IndentationError|TabError):", re.MULTILINE)


def is_missing_code_collection_error(result: SuiteExecutionResult) -> bool:
    """
    Exit 2 caused by importing code that does not exist yet. A syntax error in a test file
    is a broken test, not a failing one, so any such error disqualifies the run.
    """
    if result.exit_code != 2:
        return False
    text = f"{result.stdout}\n{result.stderr}"
    return (
        _COLLECTION_BANNER in text
        and _MISSING_CODE_ERROR.search(text) is not None
        and _BROKEN_TEST_ERROR.search(text) is None
    )


def ledger_exit_code(result: SuiteExecutionResult) -> int | None:
    """The exit code the ledger observes, or None when the run is not evidence."""
    if result.exit_code in LEDGER_EXIT_CODES:
        return result.exit_code
    if is_missing_code_collection_error(result):
        return 1
    return None


class RunTestsUnavailable(Exception):
    """RunTests could not run the suite at all (no workspace, runner failure, timeout)."""


def pytest_command(test_path: str) -> str:
    return f"PYTHONPATH=. python -m pytest {shlex.quote(test_path)} -vv --tb=short"


def run_tests_timeout_seconds() -> float:
    """The Bash tool's default timeout (`BASH_DEFAULT_TIMEOUT_MS`), in seconds."""
    from app.loop.tools.bash import get_default_bash_timeout_ms

    return get_default_bash_timeout_ms() / 1000


async def _default_test_runner(test_path: str, context: ToolContext) -> SuiteExecutionResult:
    """
    Run pytest through `context.test_runner` if one is attached, else in the context's
    workspace. Raises `RunTestsUnavailable` when neither exists or execution fails.
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
        exit_code = getattr(out, "exit_code", None)
        if not isinstance(exit_code, int):
            raise RunTestsUnavailable("the attached test runner returned no exit code")
        return SuiteExecutionResult(exit_code=exit_code)

    ws: Any = getattr(context, "workspace", None)
    if ws is None or not hasattr(ws, "execute"):
        raise RunTestsUnavailable("no workspace is available to run the tests in")
    try:
        res = ws.execute(pytest_command(test_path), timeout=run_tests_timeout_seconds())
    except Exception as exc:
        raise RunTestsUnavailable(f"pytest could not be executed in the workspace: {exc}") from exc
    return SuiteExecutionResult(exit_code=res.exit_code, stdout=res.stdout, stderr=res.stderr)


def format_output(exec_result: SuiteExecutionResult, header: str) -> str:
    body = f"{header}\n\n--- STDOUT ---\n{exec_result.stdout}"
    if exec_result.stderr.strip():
        body += f"\n--- STDERR ---\n{exec_result.stderr}"
    return body


def build_run_tests_tool(
    test_runner: TestRunner | Callable[[str], Awaitable[SuiteExecutionResult] | SuiteExecutionResult] | None = None,
    vars: Mapping[str, Any] | None = None,
) -> BuiltTool:
    """
    Build a RunTests tool instance with an optional custom runner.
    """
    runner_fn: Any = test_runner if test_runner is not None else _default_test_runner

    async def call_run_tests(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        test_path = str(input_args.get("test_path") or ".")

        try:
            try:
                res = runner_fn(test_path, context)
            except TypeError:
                res = runner_fn(test_path)
            if inspect.isawaitable(res):
                exec_result: SuiteExecutionResult = await res
            else:
                exec_result = res
        except RunTestsUnavailable as exc:
            return ToolResult(
                content=f"RunTests could not run the tests: {exc}. The TDD ledger was not changed.",
                is_error=True,
            )

        code = exec_result.exit_code
        observed = ledger_exit_code(exec_result)
        if observed is None:
            reason = PYTEST_EXIT_REASONS.get(code, "pytest did not report a test outcome")
            header = f"Tests could not be evaluated (exit {code}: {reason}). The TDD ledger was not changed."
            return ToolResult(content=format_output(exec_result, header), is_error=True, exit_code=code)

        # Update the authoritative AppState phase_ledger: the only writer (Part D2)
        context.set_app_state(lambda s: replace(s, phase_ledger=s.phase_ledger.with_test_result(observed)))

        if code == 0:
            header = "All tests passed."
        elif code == 1:
            header = f"Tests failed (exit {code})."
        else:
            header = f"Tests failed (exit {code}: collection error — the code under test does not exist yet)."
        # is_error stays False on failure: red tests are normal data in TDD
        return ToolResult(content=format_output(exec_result, header), exit_code=code)

    from app.loop.prompts.loader import render_prompt
    from app.loop.prompts.registry import global_prompt_registry
    prompt_text = global_prompt_registry.get_tool_prompt("run_tests", vars) or RUN_TESTS_PROMPT
    if vars:
        prompt_text = render_prompt(prompt_text, vars)

    return build_tool(
        name="RunTests",
        prompt=prompt_text,
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
