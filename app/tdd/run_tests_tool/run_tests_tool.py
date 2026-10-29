"""
RunTests — the TDD cycle's measurement, exposed as a loop tool (Part D2).

This is the only component that observes ground truth:
"Tools already receive set_app_state, so the ledger is updated by the only component
that can observe ground truth: the test runner. No agent asserts its own progress."
(§3.3 of `docs/transition_elaboration_plan.md`)

**It reports what it observed, as claude-code's tools do (2026-10-28).** A live run's pytest failed
on `ModuleNotFoundError: No module named 'fastapi'`, RunTests called that "the code under test
does not exist yet", and the model wrote a `fastapi.py`. Upstream's Bash never reads a command's
output for meaning: `tools/BashTool/commandSemantics.ts` maps **exit codes** to `isError` and a
label, ignoring stdout/stderr, and the model sees `Exit code N` plus the raw output
(`utils/toolErrors.ts::getErrorParts`). So RunTests says only:

* the exit code and pytest's documented meaning of it (`PYTEST_EXIT_SEMANTICS`, upstream's
  `COMMAND_SEMANTICS` shape: the seam where another runner brings its own table);
* the facts pytest's own reports record: the files it reported collection errors for, the files
  it did not collect (JUnit, `app/tdd/collection_gate.py`), the skipped count from its summary,
  the coverage it measured (`app/tdd/coverage_gate.py`);
* the ledger change and the phase's rule, stated as rules;
* the raw output, last.

Never a cause, never advice.

What a run proves depends on the phase (Phase 12, `PhaseLedger.with_test_result`):
- RED ends with a run that passes, fails, or stops on collection errors pytest reports, after the
  sub-requirement's test files changed since it started. RunTests observes the change itself, by
  fingerprinting the test files (`app/tdd/suite_fingerprint.py`) against the item's baseline. A
  file that fails to import for any reason (a missing module, a SyntaxError) is a collection error
  (user decision, 2026-10-28).
- GREEN and REFACTOR end with a run that exits 0 with no skipped test and every test file collected.
  Coverage is measured on every run and reported, never gated (user decision, 2026-10-28).

Any other outcome (interrupted without collection errors, an internal error, a usage error, no
tests collected, a timeout, no workspace) is not evidence: an error result, the ledger untouched.
A background worker's run (`ToolContext.shares_app_state` is `False`, upstream's
`shareSetAppState: !isAsync`) reports the same observations and changes no ledger.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from app.tdd.collection_gate import (
    JUNIT_REPORT_PATH,
    collected_files,
    collection_error_files,
    junit_options,
    uncollected_test_files,
)
from app.tdd.coverage_gate import (
    COVERAGE_CONFIG,
    COVERAGE_CONFIG_PATH,
    COVERAGE_REPORT_PATH,
    UNAVAILABLE,
    CoverageReading,
    CoverageStatus,
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
    #: The files pytest recorded collection errors for (its JUnit report); `None` when unknown.
    collection_errors: frozenset[str] | None = None
    __test__: bool = False


TestRunner = Callable[[str, ToolContext], Awaitable[SuiteExecutionResult] | SuiteExecutionResult]


class Outcome(Enum):
    """What a run is, as evidence. `None` (no outcome) is a run that is not evidence."""

    PASSED = "passed"
    FAILED = "failed"
    COLLECTION_ERRORS = "collection errors"


@dataclass(frozen=True, slots=True)
class ExitSemantic:
    """
    Upstream's `CommandSemantic` result (`tools/BashTool/commandSemantics.ts`): what an exit code
    means, whether it is an error, and here what it is as TDD evidence. Read from the code alone.
    """

    meaning: str
    is_error: bool
    outcome: Outcome | None


#: pytest's documented exit codes (`pytest.ExitCode`). Exit 1 is informational, as upstream's grep
#: exit 1 is: failing tests are data in TDD, not a tool error.
PYTEST_EXIT_SEMANTICS: dict[int, ExitSemantic] = {
    0: ExitSemantic("all tests passed", False, Outcome.PASSED),
    1: ExitSemantic("some tests failed", False, Outcome.FAILED),
    2: ExitSemantic("test execution was interrupted", True, None),
    3: ExitSemantic("pytest hit an internal error", True, None),
    4: ExitSemantic("pytest was invoked incorrectly (usage error)", True, None),
    5: ExitSemantic("no tests were collected", True, None),
}


def exit_semantic(exit_code: int) -> ExitSemantic:
    """The table's entry, else upstream's `DEFAULT_SEMANTIC`: any other non-zero code is an error."""
    return PYTEST_EXIT_SEMANTICS.get(exit_code, ExitSemantic(f"pytest exited with code {exit_code}", True, None))


def run_outcome(result: SuiteExecutionResult) -> tuple[Outcome | None, bool]:
    """
    `(outcome, is_error)`. Exit 2 is a collection-error outcome, and no error, only when pytest's
    report recorded collection errors: pytest stopped there, which RED counts as a run.
    """
    semantic = exit_semantic(result.exit_code)
    if result.exit_code == 2 and result.collection_errors:
        return Outcome.COLLECTION_ERRORS, False
    return semantic.outcome, semantic.is_error


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
                                collected_files=collected_files(junit), collection_errors=collection_error_files(junit))


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


#: Each phase's rule, stated as a rule in every result (2026-10-28).
PHASE_RULES: dict[TddPhase, str] = {
    TddPhase.RED: ("RED ends with a run that passes, fails, or stops on collection errors pytest reports, after "
                   "the sub-requirement's test files changed"),
    TddPhase.GREEN: ("GREEN ends with a run in which every test passes (exit 0), none is skipped and every test "
                     "file is collected"),
    TddPhase.REFACTOR: ("REFACTOR ends with a run in which every test passes (exit 0), none is skipped and every "
                        "test file is collected"),
}

#: A run that is not evidence, whatever the phase.
NOT_EVIDENCE = ("TDD ledger: unchanged. A run is evidence only when it passes, fails, or stops on collection errors "
                "pytest reports; this one did not.")

#: A background worker's run (`shareSetAppState: !isAsync`): its app-state writes are discarded.
BACKGROUND_LEDGER = "TDD ledger: not changed by a background agent's run."

NO_OUTPUT = "(RunTests completed with no output)"


@dataclass(frozen=True, slots=True)
class RunFacts:
    """What pytest's reports recorded about a run, beside its exit code."""

    collection_errors: tuple[str, ...]
    uncollected: tuple[str, ...]
    skipped: int
    coverage: CoverageReading


def coverage_line(coverage: CoverageReading) -> str:
    if coverage.status == CoverageStatus.MEASURED:
        return f"Coverage: {coverage.percent:.1f}% (line + branch)"
    if coverage.status == CoverageStatus.NO_CODE:
        return "Coverage: no production code to measure"
    return "Coverage: not measured"


def observation_lines(result: SuiteExecutionResult, facts: RunFacts) -> list[str]:
    """The exit code with pytest's meaning, then what its reports recorded. Nothing is inferred."""
    lines = [f"Exit code {result.exit_code} (pytest: {exit_semantic(result.exit_code).meaning})"]
    if facts.collection_errors:
        lines.append(f"pytest reported errors collecting: {', '.join(facts.collection_errors)}")
    if facts.uncollected:
        lines.append(f"Not collected by pytest: {', '.join(facts.uncollected)}")
    if facts.skipped:
        lines.append(f"Skipped: {facts.skipped}")
    lines.append(coverage_line(facts.coverage))
    return lines


def raw_output(result: SuiteExecutionResult) -> str:
    """stdout, then stderr, as upstream renders a command's output: leading blank lines and trailing space go."""
    parts = [text for text in (result.stdout, result.stderr) if text.strip()]
    text = "\n".join(parts)
    text = re.sub(r"^(\s*\n)+", "", text).rstrip()
    return text or NO_OUTPUT


def render_result(result: SuiteExecutionResult, facts: RunFacts, ledger_line: str) -> str:
    return "\n".join([*observation_lines(result, facts), ledger_line]) + "\n\n" + raw_output(result)


#: How a run that did not pass is named in the ledger line.
_NOT_PASSED: dict[Outcome, str] = {
    Outcome.FAILED: "this run failed",
    Outcome.COLLECTION_ERRORS: "this run had collection errors",
}


def ledger_line(before: PhaseLedger, after: PhaseLedger, *, outcome: Outcome, facts: RunFacts) -> str:
    """The ledger change, the phase's rule, and the observed facts that met it or did not."""
    phase = TddPhase(before.phase)
    rule = PHASE_RULES[phase]
    if phase == TddPhase.RED:
        if after.phase != TddPhase.RED:
            return f"TDD ledger: RED → GREEN. {rule}: they changed, and this run counts."
        why = "a test file was not collected" if facts.uncollected else "they have not changed"
        return f"TDD ledger: still RED. {rule}: {why}."
    unmet = []
    if outcome != Outcome.PASSED:
        unmet.append(_NOT_PASSED[outcome])
    if facts.skipped:
        unmet.append(f"{facts.skipped} test(s) were skipped")
    if facts.uncollected:
        unmet.append("a test file was not collected")
    if unmet:
        return f"TDD ledger: still {phase.value}. {rule}: {', '.join(unmet)}."
    if phase == TddPhase.GREEN:
        return f"TDD ledger: GREEN → REFACTOR. {rule}: this run met it."
    return f"TDD ledger: REFACTOR passed, so this sub-requirement's cycle is complete. {rule}: this run met it."


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
            return ToolResult(content=f"RunTests could not run the tests: {exc}\nTDD ledger: unchanged.", is_error=True)

        workspace = getattr(context, "workspace", None)
        outcome, is_error = run_outcome(exec_result)
        skipped = exec_result.skipped if exec_result.skipped is not None else skipped_count(
            f"{exec_result.stdout}\n{exec_result.stderr}")
        uncollected = await asyncio.to_thread(
            uncollected_test_files, workspace, test_path, exec_result.collected_files)
        facts = RunFacts(collection_errors=tuple(sorted(exec_result.collection_errors or ())),
                         uncollected=tuple(uncollected), skipped=skipped, coverage=exec_result.coverage)
        code = exec_result.exit_code
        if outcome is None:
            return ToolResult(content=render_result(exec_result, facts, NOT_EVIDENCE), is_error=True, exit_code=code)
        if not context.shares_app_state:
            return ToolResult(content=render_result(exec_result, facts, BACKGROUND_LEDGER), is_error=is_error,
                              exit_code=code)

        before = context.get_app_state()
        phase = before.phase_ledger.phase
        changed = False
        if phase == TddPhase.RED:
            current = await asyncio.to_thread(fingerprint_test_files, workspace)
            # A test file pytest did not collect ran nothing, so RED gets no evidence from the run.
            changed = have_tests_changed(current, before.tdd_item_test_baseline) and not uncollected
        # The ledger's input: 0 a pass, 1 a non-pass. In GREEN and REFACTOR a skipped test or an
        # uncollected file keeps a run from being a pass (Phase 13 and 2026-10-28, user decisions).
        passes = outcome == Outcome.PASSED and (phase == TddPhase.RED or (not skipped and not uncollected))
        observed = 0 if passes else 1
        coverage = exec_result.coverage
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
        line = ledger_line(before.phase_ledger, after, outcome=outcome, facts=facts)
        return ToolResult(content=render_result(exec_result, facts, line), is_error=is_error, exit_code=code)

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
