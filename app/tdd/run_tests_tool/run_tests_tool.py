"""
RunTests — the TDD cycle's measurement, exposed as a loop tool (Part D2).

This is the only component that observes ground truth:
"Tools already receive set_app_state, so the ledger is updated by the only component
that can observe ground truth: the test runner. No agent asserts its own progress."
(§3.3 of `docs/transition_elaboration_plan.md`)

**It runs the project's declared test command, in any language (2026-11-03).** The project declares
its `test` command in `.tddagents/project.json` (`app/tdd/project_config.py`), approved with the plan.
RunTests always runs the whole suite (`test_path` is gone, user decision): it discards the previous
reports, runs the command with `TDDAGENTS_JUNIT_REPORT` (and, when declared,
`TDDAGENTS_COVERAGE_REPORT`) naming where to write them under `.tddagents/reports/`, then reads the
JUnit report (`app/tdd/junit_report.py`) and the coverage report (`app/tdd/coverage_gate.py`). With
no declaration while a sub-requirement is current it refuses: the project declares no test command.

**It reports what it observed, as claude-code's tools do (2026-10-28).** A live run's pytest failed
on `ModuleNotFoundError: No module named 'fastapi'`, RunTests called that "the code under test
does not exist yet", and the model wrote a `fastapi.py`. Upstream's Bash never reads a command's
output for meaning: `tools/BashTool/commandSemantics.ts` maps **exit codes** to `isError` and a
label, ignoring stdout/stderr, and the model sees `Exit code N` plus the raw output
(`utils/toolErrors.ts::getErrorParts`). So RunTests says only:

* the command, the exit code and its shell meaning (`app/tdd/run_judgement.py::SHELL_EXIT_SEMANTICS`);
* what the JUnit report records (tests, failures, errors, skips, and each failed or errored test with
  its message), the declared test files the run did not run, the files it ran that the declaration does
  not cover, or that the files are unknown (the report names none, and the project's `listTests` command,
  when declared, did not list them: `app/tdd/test_listing.py`), and the coverage measured;
* the ledger change and the phase's rule, stated as rules;
* the raw output, last.

Never a cause, never advice.

What a run proves is one pure judgement (`app/tdd/run_judgement.py::judge`), shared with the session's
clean final run. RED ends with a completed run after the sub-requirement's test files changed since
it started (fingerprinted, `app/tdd/suite_fingerprint.py`); GREEN and REFACTOR end with a pass: exit
0, a report recording tests, none failed, errored or skipped, every declared test file run, and no
file run that the declaration does not cover (strict collection, 2026-11-08: files that stay unknown are
not a pass).
Coverage is measured on every run and reported, never gated (user decision, 2026-10-28).

**The session's test floor** (2026-11-08, user decisions): every evidence run records its test count, its
count per file the report names and the test files' fingerprint. The floor is a count per test file
(`ledger.TestFloor`, `next_test_floor`), across sub-requirements, and its total is the sum. A run with fewer
tests than the total is not a pass in GREEN (the files that fell short named), is accepted in REFACTOR only
when the test files changed since the floor was written (which then lowers it), and is stated in RED, where
a run that counts lowers the floor only for the files the sub-requirement changed (each file's content hash
when the item started, `AppState.tdd_item_test_hashes`). RunTests is the floor's only writer, as it is the
ledger's.

Any other outcome (not executable, not found, killed, a timeout, exit 0 without a report or without
tests, no workspace) is not evidence: an error result, the ledger untouched. A background worker's
run (`ToolContext.shares_app_state` is `False`, upstream's `shareSetAppState: !isAsync`) reports the
same observations and changes no ledger.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.tdd.collection_gate import Collection, observe_collection, resolve_ran
from app.tdd.coverage_gate import (
    UNAVAILABLE,
    CoverageReading,
    CoverageStatus,
    production_filter,
    read_coverage,
)
from app.tdd.junit_report import JUnitReport, parse_junit_report
from app.tdd.ledger import PhaseLedger, TddPhase, TestFloor, TestRunRecord
from app.tdd.project_config import (
    COVERAGE_REPORT_ENV,
    JUNIT_REPORT_ENV,
    ProjectConfig,
    invalid_project_config_note,
)
from app.tdd.run_judgement import NOT_RUN, UNDECLARED, Judgement, Outcome, judge
from app.tdd.suite_fingerprint import (
    file_changed,
    fingerprint_test_files,
    have_tests_changed,
    project_files,
    hash_test_files,
)
from app.tdd.test_listing import TestListing, run_test_listing
from app.tool import BuiltTool, ToolContext, ToolResult, build_tool
from app.utils.frontmatter_parser import parse_markdown_frontmatter

PROMPT_PATH = Path(__file__).with_name("prompt.md")

#: Why RunTests cannot run anything: the project has no approved configuration (fail closed).
NO_TEST_COMMAND = "the project declares no test command (its configuration is approved with the plan)"


@dataclass(frozen=True, slots=True)
class SuiteExecutionResult:
    """One run of the test command: its exit code, output and the reports it wrote."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    coverage: CoverageReading = UNAVAILABLE
    #: The JUnit report the command wrote; `None` when it wrote none (or an unreadable one).
    report: JUnitReport | None = None
    #: The command that ran.
    command: str = ""
    #: The project's `listTests` run, when the report named no files and the project declares one.
    listing: TestListing | None = None
    __test__: bool = False


TestRunner = Callable[[ToolContext], Awaitable[SuiteExecutionResult] | SuiteExecutionResult]


class RunTestsUnavailable(Exception):
    """RunTests could not run the suite at all (no configuration, no workspace, execution failure, timeout)."""


def run_tests_timeout_seconds() -> float:
    """The Bash tool's default timeout (`BASH_DEFAULT_TIMEOUT_MS`), in seconds."""
    from app.tools.bash_tool.bash_tool import get_default_bash_timeout_ms

    return get_default_bash_timeout_ms() / 1000


def _host_path(ws: Any, path: str) -> str:
    """A project-relative report path as the command sees it: absolute when the workspace has a root."""
    root = getattr(ws, "root", None)
    return str(Path(root) / path) if root is not None else path


def _discard(ws: Any, path: str) -> None:
    """Remove a report, leaving its directory in place so the command can write there."""
    try:
        ws.write_file(path, "")
        ws.delete_file(path)
    except Exception:  # noqa: BLE001, S110 - a report that cannot be prepared is simply not read back
        pass


def _read_and_discard(ws: Any, path: str) -> str | None:
    try:
        text: str | None = str(ws.read_file(path))
    except Exception:  # noqa: BLE001 - no report is a reading of its own
        text = None
    _discard(ws, path)
    return text


def report_environment(ws: Any, config: ProjectConfig) -> dict[str, str]:
    """The report variables the test command is run with."""
    env = {JUNIT_REPORT_ENV: _host_path(ws, config.junit_report_path)}
    if config.coverage_report is not None:
        env[COVERAGE_REPORT_ENV] = _host_path(ws, config.coverage_report.report_path)
    return env


def run_suite(ws: Any, config: ProjectConfig, *, timeout: float | None = None,
              env: Mapping[str, str] | None = None) -> SuiteExecutionResult:
    """
    The whole suite in the workspace, exactly as RunTests runs it: the reports discarded, the declared
    test command run with the report variables, the reports read back and discarded. The session's
    final run uses it too, in a clean copy of the project (`app/tdd/final_run.py`). When the report records
    tests but names no files, the declared `listTests` command runs next, with the same environment.
    """
    if ws is None or not hasattr(ws, "execute"):
        raise RunTestsUnavailable("no workspace is available to run the tests in")
    coverage_path = config.coverage_report.report_path if config.coverage_report is not None else None
    for path in (config.junit_report_path, coverage_path):
        if path is not None:
            _discard(ws, path)
    environment = {**(env or {}), **report_environment(ws, config)}
    limit = timeout if timeout is not None else run_tests_timeout_seconds()
    try:
        res = ws.execute(config.test, timeout=limit, env=environment)
    except Exception as exc:
        raise RunTestsUnavailable(f"the test command could not be executed in the workspace: {exc}") from exc
    root = getattr(ws, "root", None)
    report = parse_junit_report(_read_and_discard(ws, config.junit_report_path), root)
    coverage = UNAVAILABLE
    if config.coverage_report is not None and coverage_path is not None:
        rules = config.rules
        include = production_filter(lambda path: rules.is_test_code(path) or rules.is_excluded(path), root)
        coverage = read_coverage(_read_and_discard(ws, coverage_path), config.coverage_report.format, include)
    listing = None
    if config.list_tests is not None and report is not None and report.tests and _names_no_files(ws, config, report):
        listing = run_test_listing(ws, config, timeout=limit, env=environment)
    return SuiteExecutionResult(exit_code=res.exit_code, stdout=res.stdout, stderr=res.stderr, coverage=coverage,
                                report=report, command=config.test, listing=listing)


def _names_no_files(ws: Any, config: ProjectConfig, report: JUnitReport) -> bool:
    """Whether the report names no files (`resolve_ran`), against the project's listing."""
    files = project_files(ws, config.rules)
    return resolve_ran(report, frozenset(files) if files is not None else None, config.rules) is None


async def _default_test_runner(context: ToolContext) -> SuiteExecutionResult:
    """
    Run the declared suite through `context.test_runner` if one is attached, else in the context's
    workspace. Raises `RunTestsUnavailable` when there is no declaration or execution fails.
    """
    custom_runner: Any = getattr(context, "test_runner", None)
    if callable(custom_runner):
        res = custom_runner(context)
        out = await res if inspect.isawaitable(res) else res
        if isinstance(out, SuiteExecutionResult):
            return out
        exit_code = getattr(out, "exit_code", None)
        if not isinstance(exit_code, int):
            raise RunTestsUnavailable("the attached test runner returned no exit code")
        return SuiteExecutionResult(exit_code=exit_code)
    config = context.get_app_state().project_config
    if config is None:
        raise RunTestsUnavailable(NO_TEST_COMMAND)
    return await asyncio.to_thread(run_suite, getattr(context, "workspace", None), config)


#: Each phase's rule, stated as a rule in every result (2026-10-28, reworded 2026-11-03).
PHASE_RULES: dict[TddPhase, str] = {
    TddPhase.RED: ("RED ends with a completed run of the test command (its tests pass or fail), in which no "
                   "declared test file is named as not run and no file the declaration does not cover ran, after "
                   "the sub-requirement's test files changed"),
    TddPhase.GREEN: ("GREEN ends with a run that exits 0, whose JUnit report records tests, none failed, errored "
                     "or skipped, and in which every declared test file is named as run (by the JUnit report, else "
                     "by the listTests command) and no file the declaration does not cover ran"),
    TddPhase.REFACTOR: ("REFACTOR ends with a run that exits 0, whose JUnit report records tests, none failed, "
                        "errored or skipped, and in which every declared test file is named as run (by the JUnit "
                        "report, else by the listTests command) and no file the declaration does not cover ran"),
}

#: A run that is not evidence, whatever the phase.
NOT_EVIDENCE = ("TDD ledger: unchanged. A run is evidence only when the test command completes: it fails, or it "
                "exits 0 with a JUnit report that records tests; this one did not.")

#: A background worker's run (`shareSetAppState: !isAsync`): its app-state writes are discarded.
BACKGROUND_LEDGER = "TDD ledger: not changed by a background agent's run."

NO_OUTPUT = "(RunTests completed with no output)"


def coverage_line(coverage: CoverageReading) -> str:
    if coverage.status == CoverageStatus.MEASURED:
        return f"Coverage: {coverage.percent:.1f}% (line + branch)"
    if coverage.status == CoverageStatus.NO_CODE:
        return "Coverage: no production code to measure"
    return "Coverage: not measured"


def report_line(report: JUnitReport | None) -> str:
    if report is None:
        return "JUnit report: none was written"
    return (f"JUnit report: {report.tests} test(s), {report.failures} failed, {report.errors} errored, "
            f"{report.skipped} skipped")


def failed_case_lines(report: JUnitReport | None) -> list[str]:
    """One line per failed or errored case the report records, in its order, with its message verbatim."""
    if report is None:
        return []
    lines = []
    for case in report.failed_cases:
        kind = "Failed test" if case.outcome == "failure" else "Errored test"
        line = f"{kind}: {case.label}"
        if case.file and case.file not in case.label:
            line += f" ({case.file})"
        if case.message:
            line += f": {case.message}"
        lines.append(line)
    return lines


def collection_lines(collection: Collection | None) -> list[str]:
    """What the run collected against the declaration: the files that did not run, undeclared or unknown."""
    if collection is None:
        return []
    lines = []
    listing = collection.listing
    if listing is not None:
        line = f"listTests: {listing.command}"
        if listing.fact is not None:
            line += f" ({listing.fact})"
        lines.append(line)
    if collection.uncollected:
        lines.append(f"Declared test files the run did not run: {', '.join(collection.uncollected)}")
    if collection.undeclared:
        lines.append(f"Files the run ran that the declaration does not cover: {', '.join(collection.undeclared)}")
    if collection.unknown:
        lines.append(f"Declared test files the run did not run: unknown ({'; '.join(collection.unknown)})")
    return lines


def observation_lines(result: SuiteExecutionResult, judgement: Judgement,
                      collection: Collection | None = None) -> list[str]:
    """The command, its exit code with its shell meaning, then what the reports recorded. Nothing is inferred."""
    lines = [f"Exit code {result.exit_code} ({judgement.meaning})"]
    if result.command:
        lines.insert(0, f"Command: {result.command}")
    lines.append(report_line(result.report))
    lines.extend(failed_case_lines(result.report))
    lines.extend(collection_lines(collection))
    lines.append(coverage_line(result.coverage))
    return lines


def raw_output(result: SuiteExecutionResult) -> str:
    """stdout, then stderr, as upstream renders a command's output: leading blank lines and trailing space go."""
    parts = [text for text in (result.stdout, result.stderr) if text.strip()]
    text = "\n".join(parts)
    text = re.sub(r"^(\s*\n)+", "", text).rstrip()
    return text or NO_OUTPUT


def render_result(result: SuiteExecutionResult, judgement: Judgement, ledger_text: str,
                  collection: Collection | None = None) -> str:
    lines = [*observation_lines(result, judgement, collection), ledger_text]
    return "\n".join(lines) + "\n\n" + raw_output(result)


#: The ledger line when no sub-requirement is current: the phase rules do not apply then
#: (user decision 2026-10-31), so the run is reported without a rule it could meet.
NO_CURRENT_ITEM_LEDGER = (
    "TDD ledger: no sub-requirement is current, so the phase rules do not apply and this run is "
    "not evidence for a cycle."
)


def ledger_line(before: PhaseLedger, after: PhaseLedger, judgement: Judgement) -> str:
    """The ledger change, the phase's rule, and the observed facts that met it or did not."""
    phase = TddPhase(before.phase)
    rule = PHASE_RULES[phase]
    stated = f" Also observed: {'; '.join(judgement.stated)}." if judgement.stated else ""
    if phase == TddPhase.RED:
        if after.phase != TddPhase.RED:
            return f"TDD ledger: RED → GREEN. {rule}: they changed, and this run counts.{stated}"
        blocking = [fact for fact in judgement.unmet if fact in (NOT_RUN, UNDECLARED)]
        why = ", ".join(blocking) if blocking else "they have not changed"
        return f"TDD ledger: still RED. {rule}: {why}.{stated}"
    if judgement.unmet:
        return f"TDD ledger: still {phase.value}. {rule}: {', '.join(judgement.unmet)}.{stated}"
    if phase == TddPhase.GREEN:
        return f"TDD ledger: GREEN → REFACTOR. {rule}: this run met it.{stated}"
    return (f"TDD ledger: REFACTOR passed, so this sub-requirement's cycle is complete. {rule}: this run met it."
            f"{stated}")


def next_test_floor(floor: TestFloor | None, record: TestRunRecord, judgement: Judgement, red_counted: bool,
                    item_hashes: Mapping[str, str] | None,
                    hashes: Mapping[str, str] | None) -> TestFloor | None:
    """
    The session's test floor after `record` (`AppState.test_floor`, user decisions 2026-11-08). Only a run
    whose report names its files (`record.file_tests`) moves it:

    * a RED run that counts (`red_counted`: the test files changed and the run met RED's rule) lowers it only
      for the files this sub-requirement changed: a file whose content changed since the item started
      (`item_hashes` against `hashes`, `file_changed`) takes the run's count, so a file deleted this item
      goes; every other file keeps its floor, raised to the run's count when that is higher;
    * any other run that records as many tests as the floor's total or more replaces it with its counts;
    * fewer tests replace it only in an accepted decrease: a REFACTOR run that met its rule (which it can only
      with changed test files);
    * anything else leaves it as it is.
    """
    if not record.tests or record.file_tests is None:
        return floor
    ran = dict(record.file_tests)
    if record.phase == TddPhase.RED and red_counted and floor is not None:
        merged = {path: ran.get(path, 0) if file_changed(path, hashes, item_hashes)
                  else max(floor.count(path), ran.get(path, 0))
                  for path in {*dict(floor.files), *ran}}
        return TestFloor.of(merged, record.test_fingerprint)
    if floor is None or record.tests >= floor.total:
        return TestFloor.of(ran, record.test_fingerprint)
    if record.phase == TddPhase.REFACTOR and judgement.meets_phase:
        return TestFloor.of(ran, record.test_fingerprint)
    return floor


def build_run_tests_tool(test_runner: TestRunner | None = None) -> BuiltTool:
    """A RunTests tool, with an optional runner standing in for the declared command (tests)."""
    runner_fn: Any = test_runner if test_runner is not None else _default_test_runner

    async def call_run_tests(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        before = context.get_app_state()
        config = before.project_config
        if config is None and before.tdd_plan.current is not None:
            note = invalid_project_config_note(before.project_config_issues)
            reason = f"{NO_TEST_COMMAND}.{note}" if note else NO_TEST_COMMAND
            return ToolResult(
                content=f"RunTests could not run the tests: {reason}\nTDD ledger: unchanged.",
                is_error=True)
        try:
            res = runner_fn(context)
            exec_result: SuiteExecutionResult = await res if inspect.isawaitable(res) else res
        except RunTestsUnavailable as exc:
            return ToolResult(content=f"RunTests could not run the tests: {exc}\nTDD ledger: unchanged.", is_error=True)

        workspace = getattr(context, "workspace", None)
        rules = config.rules if config is not None else None
        report = exec_result.report
        code = exec_result.exit_code
        collection = await asyncio.to_thread(observe_collection, workspace, rules, report, exec_result.listing)
        current = await asyncio.to_thread(fingerprint_test_files, workspace, config)
        hashes = await asyncio.to_thread(hash_test_files, workspace, config)
        state_now = context.get_app_state()
        phase = TddPhase(state_now.phase_ledger.phase)
        judgement = judge(code, report, collection, phase, state_now.test_floor, current)
        if judgement.outcome is None:
            return ToolResult(content=render_result(exec_result, judgement, NOT_EVIDENCE, collection), is_error=True,
                              exit_code=code)
        if not context.shares_app_state:
            return ToolResult(content=render_result(exec_result, judgement, BACKGROUND_LEDGER, collection),
                              is_error=judgement.is_error, exit_code=code)

        before = context.get_app_state()
        changed = False
        if phase == TddPhase.RED:
            # A declared test file that did not run, or an undeclared one that did, gives RED no evidence.
            changed = have_tests_changed(current, before.tdd_item_test_baseline) and judgement.meets_phase
        # The ledger's input: 0 a pass, 1 a non-pass. In GREEN and REFACTOR a skipped test or a
        # declared test file that did not run keeps a run from being a pass (Phase 13, 2026-10-28).
        observed = 0 if judgement.outcome == Outcome.PASSED and judgement.meets_phase else 1
        coverage = exec_result.coverage
        after = before.phase_ledger.with_test_result(observed, tests_changed=changed, coverage=coverage)

        # Update the authoritative AppState phase_ledger: the only writer (Part D2)
        record = TestRunRecord(command=exec_result.command, exit_code=code, stdout=exec_result.stdout,
                               stderr=exec_result.stderr, meaning=judgement.meaning, phase=phase,
                               tests=report.tests if report is not None else None, test_fingerprint=current,
                               file_tests=collection.counts if collection is not None else None)
        context.set_app_state(
            lambda s: replace(
                s, phase_ledger=s.phase_ledger.with_test_result(observed, tests_changed=changed, coverage=coverage),
                last_test_run=record,
                test_floor=next_test_floor(s.test_floor, record, judgement, changed, s.tdd_item_test_hashes, hashes))
        )
        line = (ledger_line(before.phase_ledger, after, judgement)
                if before.tdd_plan.current is not None else NO_CURRENT_ITEM_LEDGER)
        return ToolResult(content=render_result(exec_result, judgement, line, collection),
                          is_error=judgement.is_error, exit_code=code)

    prompt_text = parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1]

    return build_tool(
        name="RunTests",
        prompt=prompt_text,
        input_schema={"type": "object", "properties": {}},
        description=lambda args: "Run the project's test suite",
        is_read_only=lambda args: True,
        is_concurrency_safe=lambda args: False,
        is_implementation_writer=False,
        is_test_writer=False,
        call=call_run_tests,
    )


RunTests = build_run_tests_tool()
