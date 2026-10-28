"""
RunTests — the TDD cycle's measurement, exposed as a loop tool (Part D2).

This is the only component that observes ground truth:
"Tools already receive set_app_state, so the ledger is updated by the only component
that can observe ground truth: the test runner. No agent asserts its own progress."
(§3.3 of `docs/transition_elaboration_plan.md`)

What a run proves depends on the phase (Phase 12, `PhaseLedger.with_test_result`):
- RED ends with a run, pass or fail, in which the sub-requirement's test files changed since
  it started. RunTests observes that itself, by fingerprinting the test files
  (`app/tdd/suite_fingerprint.py`) against the item's baseline.
- GREEN ends with a passing run.
- REFACTOR ends with a passing run. Every run measures line + branch coverage with pytest-cov
  (`app/tdd/coverage_gate.py`) and reports it as guidance, not a gate (user decision, 2026-10-28).

Only pytest's exit codes 0 (passed) and 1 (tests failed) are observations about the code
under test — plus one form of exit 2: a collection error raised because the code under test
does not exist yet (`ModuleNotFoundError`, `ImportError`, `AttributeError` while importing a
test), which counts as failing tests. Every other outcome —
2 interrupted, 3 internal error, 4 usage error, 5 no tests collected, a timeout, a missing
workspace, a crashed runner — says nothing about Red or Green, so it is reported as an
error and the ledger is left untouched. In particular "no tests collected" can never confirm
RED. The tool fails closed: with no workspace and no injected runner it refuses rather than
pretending the suite passed.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.tdd.collection_gate import (
    JUNIT_REPORT_PATH,
    collected_files,
    junit_options,
    uncollected_message,
    uncollected_test_files,
)
from app.tdd.coverage_gate import (
    COVERAGE_CONFIG,
    COVERAGE_CONFIG_PATH,
    COVERAGE_REPORT_PATH,
    UNAVAILABLE,
    CoverageReading,
    read_coverage,
)
from app.tdd.ledger import PhaseLedger, TddPhase, TestRunRecord
from app.tdd.suite_fingerprint import fingerprint_test_files, have_tests_changed
from app.tool import BuiltTool, ToolContext, ToolResult, build_tool
from app.utils.frontmatter_parser import parse_markdown_frontmatter

PROMPT_PATH = Path(__file__).with_name("prompt.md")


@dataclass(frozen=True, slots=True)
class SuiteExecutionResult:
    """Outcome of running test suite."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    coverage: CoverageReading = UNAVAILABLE
    #: Tests pytest skipped; `None` reads the count from the output (`skipped_count`).
    skipped: int | None = None
    #: The files pytest collected (its JUnit report); `None` when unknown, which checks nothing.
    collected_files: frozenset[str] | None = None
    __test__: bool = False


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


#: True (exit 0) when the workspace has a pytest config file of its own, in pytest's own terms:
#: `pytest.ini` / `.pytest.ini` always, the others only with their pytest section.
PYTEST_CONFIG_PROBE = (
    "[ -f pytest.ini ] || [ -f .pytest.ini ] || grep -qs '^\\[tool\\.pytest' pyproject.toml "
    "|| grep -qs '^\\[pytest\\]' tox.ini || grep -qs '^\\[tool:pytest\\]' setup.cfg"
)


def pytest_command(test_path: str) -> str:
    """
    The suite's command. pytest looks for its config upward from the workspace, and a local
    workspace sits inside TDDAgents' own repository, so without a config of its own the
    generated project would run under TDDAgents' `pyproject.toml`. The rootdir is pinned to the
    workspace and, when it has no config, `-c /dev/null` stops the search (the same run locally
    and in a sandbox). Coverage is measured on every run (Phase 12): `term-missing` shows the
    model the uncovered lines, and the JSON report is what REFACTOR is judged by. The JUnit report
    names the files pytest collected (`app/tdd/test_collection.py`).
    """
    return (
        f"PYTHONPATH=. python -m pytest {shlex.quote(test_path)} -vv --tb=short -rs --rootdir=. "
        f"--cov=. --cov-config={COVERAGE_CONFIG_PATH} --cov-report=term-missing "
        f"--cov-report=json:{COVERAGE_REPORT_PATH} {junit_options()} "
        f"$({PYTEST_CONFIG_PROBE} || printf %s '-c /dev/null')"
    )


def run_tests_timeout_seconds() -> float:
    """The Bash tool's default timeout (`BASH_DEFAULT_TIMEOUT_MS`), in seconds."""
    from app.tools.bash_tool.bash_tool import get_default_bash_timeout_ms

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

    return await asyncio.to_thread(run_suite, getattr(context, "workspace", None), test_path)


def run_suite(ws: Any, test_path: str = ".") -> SuiteExecutionResult:
    """
    The suite in the workspace, exactly as RunTests runs it: its coverage config, `pytest_command`
    and the coverage report read back. The session's final run uses it too (Phase 13), so the
    result reported at the end is the one the agents' own runs saw.
    """
    if ws is None or not hasattr(ws, "execute"):
        raise RunTestsUnavailable("no workspace is available to run the tests in")
    try:
        ws.write_file(COVERAGE_CONFIG_PATH, COVERAGE_CONFIG)
        _discard(ws, COVERAGE_REPORT_PATH)
        _discard(ws, JUNIT_REPORT_PATH)
        res = ws.execute(pytest_command(test_path), timeout=run_tests_timeout_seconds())
    except Exception as exc:
        raise RunTestsUnavailable(f"pytest could not be executed in the workspace: {exc}") from exc
    try:
        report: str | None = ws.read_file(COVERAGE_REPORT_PATH)
    except Exception:  # noqa: BLE001 - no report is a reading of its own (no code, or unavailable)
        report = None
    _discard(ws, COVERAGE_REPORT_PATH)
    try:
        junit: str | None = ws.read_file(JUNIT_REPORT_PATH)
    except Exception:  # noqa: BLE001 - no report: the collected files are unknown
        junit = None
    _discard(ws, JUNIT_REPORT_PATH)
    coverage = read_coverage(report, f"{res.stdout}\n{res.stderr}")
    return SuiteExecutionResult(exit_code=res.exit_code, stdout=res.stdout, stderr=res.stderr, coverage=coverage,
                                collected_files=collected_files(junit))


def _discard(ws: Any, path: str) -> None:
    try:
        ws.delete_file(path)
    except Exception:  # noqa: BLE001, S110 - a report that is not there is already discarded
        pass


#: pytest's summary line counts skipped tests (`== 3 passed, 1 skipped in 0.1s ==`).
_SKIPPED = re.compile(r"\b(\d+) skipped\b")


def skipped_count(output: str) -> int:
    """The skipped tests pytest's last summary line reports; 0 when it reports none."""
    found = _SKIPPED.findall(output)
    return int(found[-1]) if found else 0


#: What a passing run with skipped tests is told in GREEN and REFACTOR (Phase 13).
SKIPPED_IS_NOT_PASSING = (
    "{count} test(s) were skipped, so this run does not count as passing: a skipped test checks "
    "nothing. Every test must run in a plain RunTests run: install what they need, and do not gate "
    "tests behind environment variables, markers or options (size an expensive check so that it runs "
    "in a normal test run). Then run RunTests again. The skip reasons are listed in the short test "
    "summary below."
)


def ledger_status(before: PhaseLedger, after: PhaseLedger, exit_code: int, coverage: CoverageReading) -> str:
    """What the run did to the TDD ledger, and what the phase now needs."""
    if before.phase == TddPhase.RED:
        if after.phase == TddPhase.RED:
            return ("No test file changed since this sub-requirement started, so the phase stays RED: write or "
                    "extend its tests (robust, covering every relevant scenario and edge case), then run RunTests.")
        return ("RED is done: this sub-requirement's tests were written and run. The phase is GREEN: implement "
                "the code they describe and run RunTests until every test passes.")
    if before.phase == TddPhase.GREEN:
        if exit_code != 0:
            return "The phase stays GREEN: every test must pass."
        return ("GREEN is done. The phase is REFACTOR, which is mandatory: improve the code and the tests and "
                f"raise coverage ({coverage.describe()}), then run RunTests until every test passes.")
    if exit_code != 0:
        return "REFACTOR is not done: every test must pass."
    return f"REFACTOR is done ({coverage.describe()}): this sub-requirement's cycle is complete."


def format_output(exec_result: SuiteExecutionResult, header: str) -> str:
    body = f"{header}\n\n--- STDOUT ---\n{exec_result.stdout}"
    if exec_result.stderr.strip():
        body += f"\n--- STDERR ---\n{exec_result.stderr}"
    return body


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

        before = context.get_app_state()
        changed = False
        if before.phase_ledger.phase == TddPhase.RED:
            current = await asyncio.to_thread(fingerprint_test_files, getattr(context, "workspace", None))
            changed = have_tests_changed(current, before.tdd_item_test_baseline)
        coverage = exec_result.coverage
        skipped = exec_result.skipped if exec_result.skipped is not None else skipped_count(
            f"{exec_result.stdout}\n{exec_result.stderr}")
        skips_block_a_pass = observed == 0 and skipped > 0 and before.phase_ledger.phase != TddPhase.RED
        # A test file pytest did not collect checks nothing either, in every phase (user decision).
        uncollected = await asyncio.to_thread(
            uncollected_test_files, getattr(context, "workspace", None), test_path, exec_result.collected_files)
        uncollected_block_a_pass = bool(uncollected) and observed == 0 and before.phase_ledger.phase != TddPhase.RED
        if uncollected:
            changed = False
        if skips_block_a_pass or uncollected_block_a_pass:
            # A skipped test checks nothing, so the run is not a pass (Phase 13, user decision).
            observed = 1
        after = before.phase_ledger.with_test_result(observed, tests_changed=changed, coverage=coverage)

        # Update the authoritative AppState phase_ledger: the only writer (Part D2)
        record = TestRunRecord(
            test_path=test_path, exit_code=code, stdout=exec_result.stdout, stderr=exec_result.stderr
        )
        context.set_app_state(
            lambda s: replace(
                s, phase_ledger=s.phase_ledger.with_test_result(observed, tests_changed=changed, coverage=coverage),
                last_test_run=record)
        )

        if skips_block_a_pass:
            header = f"Tests ran without failures, but {SKIPPED_IS_NOT_PASSING.format(count=skipped)}"
        elif uncollected_block_a_pass:
            header = f"Tests ran without failures, but {uncollected_message(uncollected)}"
        elif code == 0:
            header = "All tests passed."
        elif code == 1:
            header = f"Tests failed (exit {code})."
        else:
            header = f"Tests failed (exit {code}: collection error — the code under test does not exist yet)."
        if uncollected and not uncollected_block_a_pass:
            header = f"{header}\n{uncollected_message(uncollected)}"
        header = f"{header}\n{ledger_status(before.phase_ledger, after, observed, coverage)}"
        # is_error stays False on failure: a failing test is normal data in TDD
        return ToolResult(content=format_output(exec_result, header), exit_code=code)

    prompt_text = parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1]

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
