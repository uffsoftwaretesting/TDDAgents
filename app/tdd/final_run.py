"""
The session's final run, in a clean copy of the project (2026-11-03, user decision).

The CPF live run passed every TDD barrier, yet its CI failed in a fresh environment: the model had
installed a package into the session's environment and never declared it. Every RunTests run and
the old final run shared that environment, so nothing compared what was declared with what was
installed. The final run now does what a fresh checkout does:

1. the project is copied into `<thread>/final-run/project` (`shutil.copytree`) without `.git`,
   `.tddagents/` (except the approved `project.json`), the declared `exclude` patterns and whatever
   the project's root `.gitignore` ignores: no installed dependency, build output or cache crosses;
2. the environment is scrubbed: no `TDDAGENTS_ENV_FILE`, TDDAgents' own interpreter kept off `PATH`
   (`app/workspace/local.py::orchestrator_free_environment`), fresh report variables;
3. the declared `setup` runs, with upstream's maximum Bash timeout (`getMaxBashTimeoutMs`); a non-zero
   exit is "NOT passing (setup exited N)";
4. the declared `test` runs exactly as RunTests runs it (`run_suite`), and the run is judged as a
   GREEN run (`app/tdd/run_judgement.py::judge`): a dependency installed but never declared fails here;
5. `final_test_run.txt` records the setup command, exit and output, the test exit and its meaning, the
   JUnit counts, the coverage, the declared test files that did not run, and the raw output;
6. the copy is deleted.

An E2B workspace has no host folder to copy, so its final run is reported as unsupported.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pathspec

from app.tdd.collection_gate import uncollected_test_files
from app.tdd.ledger import TddPhase
from app.tdd.project_config import PROJECT_CONFIG_FILE, ProjectConfig, TestFileRules, load_project_config
from app.tdd.run_judgement import judge
from app.tdd.run_tests_tool.run_tests_tool import (
    RunTestsUnavailable,
    SuiteExecutionResult,
    coverage_line,
    raw_output,
    report_line,
    run_suite,
)
from app.utils.session_environment import ENV_FILE_VAR

FINAL_RUN_DIRNAME = "final-run"
UNSUPPORTED = "clean final run unsupported: the workspace is not a local folder"
NO_CONFIG = "not run: the project declares no test command"


@dataclass(frozen=True, slots=True)
class FinalRun:
    """What the final run observed: the one-line summary and the report's lines."""

    summary: str
    lines: tuple[str, ...]


def max_command_timeout_seconds() -> float:
    """Upstream's maximum Bash timeout (`getMaxBashTimeoutMs`), in seconds."""
    from app.tools.bash_tool.bash_tool import get_max_bash_timeout_ms

    return get_max_bash_timeout_ms() / 1000


def _gitignore(source: Path) -> pathspec.GitIgnoreSpec:
    try:
        lines = (source / ".gitignore").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    return pathspec.GitIgnoreSpec.from_lines(lines)


def copy_project(source: Path, target: Path, rules: TestFileRules) -> None:
    """
    Copy `source` to `target` without `.git`, `.tddagents/`, the declared excluded paths and what the
    root `.gitignore` ignores; the approved `project.json` is copied back into `.tddagents/`.
    """
    ignored_by_git = _gitignore(source)

    def ignore(directory: str, names: list[str]) -> set[str]:
        relative = Path(directory).relative_to(source).as_posix()
        prefix = "" if relative == "." else f"{relative}/"
        skipped = set()
        for name in names:
            path = f"{prefix}{name}"
            as_dir = f"{path}/" if os.path.isdir(os.path.join(directory, name)) else path
            if rules.is_excluded(as_dir) or ignored_by_git.match_file(as_dir):
                skipped.add(name)
        return skipped

    shutil.copytree(source, target, symlinks=True, ignore=ignore)
    config_file = source / PROJECT_CONFIG_FILE
    if config_file.is_file():
        (target / PROJECT_CONFIG_FILE).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config_file, target / PROJECT_CONFIG_FILE)


def _setup_lines(config: ProjectConfig, exit_code: int | None, output: str) -> list[str]:
    if config.setup is None:
        return ["Setup: none declared"]
    return [f"Setup: {config.setup}", f"Setup exit code: {exit_code}", "Setup output:", output or "(no output)"]


def _test_lines(result: SuiteExecutionResult, uncollected: tuple[str, ...] | None, meaning: str) -> list[str]:
    lines = [f"Test: {result.command}", f"Test exit code: {result.exit_code} ({meaning})", report_line(result.report)]
    if uncollected:
        lines.append(f"Declared test files the JUnit report does not name: {', '.join(uncollected)}")
    elif result.exit_code == 0 and result.report is not None and result.report.files is None:
        lines.append("Declared test files the JUnit report does not name: unknown (the report names no files)")
    lines.append(coverage_line(result.coverage))
    return lines


def run_final(workspace: Any, run_dir: str | os.PathLike[str]) -> FinalRun:
    """The clean final run of `workspace`'s project (see the module docstring)."""
    from app.workspace.local import LocalWorkspace

    root = getattr(workspace, "root", None)
    if getattr(workspace, "kind", None) != "local" or root is None:
        return FinalRun(UNSUPPORTED, ())
    config = load_project_config(workspace)
    if config is None:
        return FinalRun(NO_CONFIG, ())
    base = Path(run_dir) / FINAL_RUN_DIRNAME
    target = base / "project"
    shutil.rmtree(base, ignore_errors=True)
    try:
        try:
            copy_project(Path(root), target, config.rules)
        except OSError as exc:
            return FinalRun(f"could not copy the project: {exc}", ())
        clean = LocalWorkspace(target, env={ENV_FILE_VAR: ""}, run_dir=base)
        timeout = max_command_timeout_seconds()
        setup_exit: int | None = None
        setup_output = ""
        if config.setup is not None:
            try:
                setup = clean.execute(config.setup, timeout=timeout)
            except Exception as exc:  # noqa: BLE001 - a setup that cannot run is the run's result
                return FinalRun(f"NOT passing (setup could not run: {exc})", tuple(_setup_lines(config, None, "")))
            setup_exit = setup.exit_code
            setup_output = "\n".join(text for text in (setup.stdout, setup.stderr) if text.strip()).rstrip()
            if setup_exit != 0:
                return FinalRun(f"NOT passing (setup exited {setup_exit})",
                                tuple(_setup_lines(config, setup_exit, setup_output)))
        try:
            result = run_suite(clean, config, timeout=timeout)
        except RunTestsUnavailable as exc:
            return FinalRun(f"NOT passing (could not run the tests: {exc})",
                            tuple(_setup_lines(config, setup_exit, setup_output)))
        ran = result.report.files if result.report is not None and result.exit_code == 0 else None
        uncollected = uncollected_test_files(clean, config.rules, ran)
        judgement = judge(result.exit_code, result.report, uncollected, TddPhase.GREEN)
        verdict = "passed" if judgement.passed else "NOT passing"
        summary = f"{verdict} (exit {result.exit_code}: {judgement.meaning}, {result.coverage.describe()})"
        if judgement.unmet:
            summary += f"; {', '.join(judgement.unmet)}"
        if judgement.uncollected:
            summary += f": {', '.join(judgement.uncollected)}"
        lines = [*_setup_lines(config, setup_exit, setup_output), "-" * 80,
                 *_test_lines(result, judgement.uncollected, judgement.meaning), "-" * 80, raw_output(result)]
        return FinalRun(summary, tuple(lines))
    finally:
        shutil.rmtree(base, ignore_errors=True)


__all__ = ["FINAL_RUN_DIRNAME", "NO_CONFIG", "UNSUPPORTED", "FinalRun", "copy_project", "run_final"]
