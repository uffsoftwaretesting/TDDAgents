"""
The session's final run, in a clean copy of the project (2026-11-03, user decision).

The CPF live run passed every TDD barrier, yet its CI failed in a fresh environment: the model had
installed a package into the session's environment and never declared it. Every RunTests run and
the old final run shared that environment, so nothing compared what was declared with what was
installed. The final run now does what a fresh checkout does:

1. the project is copied into `<thread>/final-run/project`, file by file, from upstream's project
   listing (`project_files`: ripgrep's `--files --hidden`, as `countFilesRoundedRg` and `utils/glob.ts`
   list files), so every ignore file rg honours applies: the root and every nested `.gitignore`,
   `.ignore` and `.rgignore`, and the global git excludes. `.git` and the other version-control
   directories upstream's file index leaves out (`hooks/fileSuggestions.ts`), `.tddagents/` (except the
   approved `project.json`) and the declared `exclude` patterns never cross either: no installed
   dependency, build output or cache does;
2. the environment is scrubbed: no `TDDAGENTS_ENV_FILE`, TDDAgents' own interpreter kept off `PATH`
   (`app/workspace/local.py::orchestrator_free_environment`), fresh report variables;
3. the declared `setup` runs, with upstream's maximum Bash timeout (`getMaxBashTimeoutMs`); a non-zero
   exit is "NOT passing (setup exited N)";
4. the declared `test` runs exactly as RunTests runs it (`run_suite`), and the run is judged as a
   GREEN run (`app/tdd/run_judgement.py::judge`): a dependency installed but never declared fails here;
5. `final_test_run.txt` records the setup command, exit and output, the test exit and its meaning, the
   JUnit counts, the coverage, the declared test files that did not run, and the raw output;
6. the copy is deleted.

An E2B workspace has no host folder to copy, so its final run is reported as unsupported, and so is
one where ripgrep cannot run (`app/utils/ripgrep.py` has no fallback lister).

Divergences from upstream's listing (2026-11-03): `--no-require-git`, because TDDAgents never runs
`git init` and rg reads `.gitignore` only inside a git repository, while a fresh checkout always
honours it; and `--no-ignore-parent`, because the project is the root of every lookup and a fresh
checkout sees no ignore file above it (a `~/.gitignore` must not empty the copy). Without `--follow`,
rg lists no symbolic link, so none is copied.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from app.utils.ripgrep import RipgrepTimeoutError, RipgrepUnavailableError, rip_grep, to_relative_path
from app.utils.session_environment import ENV_FILE_VAR

FINAL_RUN_DIRNAME = "final-run"
UNSUPPORTED = "clean final run unsupported: the workspace is not a local folder"
NO_RIPGREP = "clean final run unsupported: ripgrep, which lists the project's files, could not run"
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


#: The version-control directories upstream's file index never lists (`hooks/fileSuggestions.ts`).
VCS_DIRECTORIES = (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")

#: The listing's ripgrep arguments: upstream's `--files --hidden`, the version-control directories and
#: `.tddagents/` pruned, and the two divergences the module docstring explains.
LISTING_ARGS = (
    "--files", "--hidden", "--no-require-git", "--no-ignore-parent",
    *(arg for name in (*VCS_DIRECTORIES, ".tddagents") for arg in ("--glob", f"!{name}/")),
)


def _scrubbed(root: Path, run_dir: Path) -> Any:
    """A workspace over `root` without the session's environment script (`TDDAGENTS_ENV_FILE`)."""
    from app.workspace.local import LocalWorkspace

    return LocalWorkspace(root, env={ENV_FILE_VAR: ""}, run_dir=run_dir)


def project_files(source: Path, rules: TestFileRules) -> list[str]:
    """
    The project's files as a fresh checkout has them, root-relative and sorted: ripgrep's listing
    (`LISTING_ARGS`) without the declared `exclude` patterns (`TestFileRules.is_excluded`, the matcher
    the phase tier and the fingerprint use). Raises `RipgrepUnavailableError` or `RipgrepTimeoutError`.
    """
    listed = rip_grep(_scrubbed(source, source.parent), list(LISTING_ARGS), ".")
    return sorted({path for path in map(to_relative_path, listed) if not rules.is_excluded(path)})


def copy_project(source: Path, target: Path, rules: TestFileRules) -> None:
    """Copy `project_files(source)` to `target`, then the approved `project.json` into `.tddagents/`."""
    target.mkdir(parents=True, exist_ok=True)
    for path in project_files(source, rules):
        (target / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / path, target / path)
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
        except RipgrepUnavailableError as exc:
            return FinalRun(f"{NO_RIPGREP} ({exc})", ())
        except (OSError, RipgrepTimeoutError) as exc:
            return FinalRun(f"could not copy the project: {exc}", ())
        clean = _scrubbed(target, base)
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


__all__ = ["FINAL_RUN_DIRNAME", "LISTING_ARGS", "NO_CONFIG", "NO_RIPGREP", "UNSUPPORTED", "VCS_DIRECTORIES",
           "FinalRun", "copy_project", "project_files", "run_final"]
