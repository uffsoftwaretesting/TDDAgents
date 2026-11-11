"""
The project's declared configuration: how it is set up, how its tests run, which files are tests.

TDDAgents works with every language (user decision, 2026-11-03). Claude-code itself is
language-agnostic: it runs the project's own commands and learns a language from skills. The TDD
barriers still need to know three things about a project, and the project declares them in
`.tddagents/project.json` instead of TDDAgents guessing them from built-in adapters:

* how to set it up from its manifests (`setup`) and run its whole suite (`test`). The test command
  writes a JUnit XML report to `$TDDAGENTS_JUNIT_REPORT` (or to the declared `junitReport`) and, when
  `coverageReport` is declared, a cobertura or lcov report to `$TDDAGENTS_COVERAGE_REPORT`;
* when its JUnit report names no files (surefire, `go-junit-report`, nextest), how to list the test files
  its runner collects (`listTests`, optional, 2026-11-08: `app/tdd/test_listing.py`);
* which files are tests (`testFiles`), test support (`testSupportFiles`: fixtures, helpers, test
  configuration), manifests and lockfiles (`manifestFiles`), and what is never part of the project's
  own sources (`exclude`: dependencies, build output, caches). Each is a list of gitignore patterns
  relative to the project root, matched with the `pathspec` gitignore matcher the permission rules
  use (`app/utils/permissions/filesystem.py`). `.git/` and `.tddagents/` are always excluded.
  `exclude` accepts generated directories only (user decision 2026-11-08, `_check_exclude`): every
  pattern ends in `/`, and none may cover a declared test file, judged from the patterns alone (no
  disk check: a dependency tree such as `node_modules/` ships `*.test.*` files of its own).

The Analyst proposes the configuration in the plan file's `## Project Configuration` section
(`app/tdd/plan.py::parse_project_configuration`), the user approves it with the plan, and
`ExitPlanMode` writes it here (`app/tools/exit_plan_mode_tool/`). `.tddagents/` is a protected
directory: a write there asks the user first, in every mode, as upstream's `.claude/`. An approved edit
is read at once (`app/tdd/project_config_watcher.py`), and the Edit tool refuses an edit that would
make a valid file invalid (`app/tdd/validate_project_config_edit.py`, 2026-11-08).

The schema is strict (version 1, camelCase keys): an unknown key, a wrong type or a missing
required key is an issue, and `parse_project_config` returns every issue at once, so one refusal
names them all.
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
from dataclasses import dataclass, field
from typing import Any

import pathspec

from app.utils.debug import TERMINAL

logger = logging.getLogger("TDDOrchestrator.ProjectConfig")

#: Where the approved configuration lives, inside the project's protected `.tddagents/` directory.
PROJECT_CONFIG_FILE = ".tddagents/project.json"
#: The schema version this module reads and writes.
PROJECT_CONFIG_VERSION = 1
#: Where RunTests asks the test command to write its reports (discarded before every run).
REPORTS_DIR = ".tddagents/reports"
#: The environment variables that tell the test command where to write its reports.
JUNIT_REPORT_ENV = "TDDAGENTS_JUNIT_REPORT"
COVERAGE_REPORT_ENV = "TDDAGENTS_COVERAGE_REPORT"
DEFAULT_JUNIT_REPORT = f"{REPORTS_DIR}/junit.xml"
#: The coverage report formats RunTests reads (`app/tdd/coverage_gate.py`), with their default paths.
COVERAGE_FORMATS: dict[str, str] = {
    "cobertura": f"{REPORTS_DIR}/coverage.xml",
    "lcov": f"{REPORTS_DIR}/lcov.info",
}
#: The bundled skill that knows how each ecosystem fills the configuration in.
PROJECT_CONFIG_SKILL = "tdd-project-config"
#: Directories that are never the project's own files, whatever the configuration says.
ALWAYS_EXCLUDED_DIRS = (".git", ".tddagents")

_KEYS = ("version", "setup", "test", "listTests", "junitReport", "coverageReport", "testFiles", "testSupportFiles",
         "manifestFiles", "exclude")
_COVERAGE_KEYS = ("path", "format")
_PATTERN_KEYS = ("testFiles", "testSupportFiles", "manifestFiles", "exclude")


@dataclass(frozen=True, slots=True)
class CoverageReportConfig:
    """Where the test command writes its coverage report, and in which format."""

    format: str
    path: str | None = None

    @property
    def report_path(self) -> str:
        """The declared path, else the format's default under `REPORTS_DIR`."""
        return self.path or COVERAGE_FORMATS[self.format]


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """`.tddagents/project.json`, schema version 1 (see the module docstring)."""

    test: str
    test_files: tuple[str, ...]
    version: int = PROJECT_CONFIG_VERSION
    setup: str | None = None
    junit_report: str | None = None
    coverage_report: CoverageReportConfig | None = None
    test_support_files: tuple[str, ...] = ()
    manifest_files: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    #: The command whose stdout lists the collected test files, one per line (`app/tdd/test_listing.py`).
    list_tests: str | None = None
    #: The matchers built from the patterns, once per configuration.
    _rules: list[TestFileRules] = field(default_factory=list, init=False, compare=False, repr=False)

    @property
    def junit_report_path(self) -> str:
        return self.junit_report or DEFAULT_JUNIT_REPORT

    @property
    def rules(self) -> TestFileRules:
        if not self._rules:
            self._rules.append(TestFileRules.of(self))
        return self._rules[0]

    def to_dict(self) -> dict[str, Any]:
        """The camelCase JSON object, with the optional keys that are unset left out."""
        data: dict[str, Any] = {"version": self.version}
        if self.setup is not None:
            data["setup"] = self.setup
        data["test"] = self.test
        if self.list_tests is not None:
            data["listTests"] = self.list_tests
        if self.junit_report is not None:
            data["junitReport"] = self.junit_report
        if self.coverage_report is not None:
            coverage: dict[str, str] = {"format": self.coverage_report.format}
            if self.coverage_report.path is not None:
                coverage["path"] = self.coverage_report.path
            data["coverageReport"] = coverage
        data["testFiles"] = list(self.test_files)
        data["testSupportFiles"] = list(self.test_support_files)
        data["manifestFiles"] = list(self.manifest_files)
        data["exclude"] = list(self.exclude)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"


def _spec(patterns: tuple[str, ...]) -> pathspec.GitIgnoreSpec:
    return pathspec.GitIgnoreSpec.from_lines(patterns)


@dataclass(frozen=True, slots=True)
class TestFileRules:
    """
    The declared patterns as matchers. Every method takes a path relative to the project root,
    in POSIX form (`project_relative_path`); a directory is named with a trailing `/`.
    """

    test_files: pathspec.GitIgnoreSpec
    test_support_files: pathspec.GitIgnoreSpec
    manifest_files: pathspec.GitIgnoreSpec
    excluded: pathspec.GitIgnoreSpec
    __test__ = False

    @classmethod
    def of(cls, config: ProjectConfig) -> TestFileRules:
        return cls(_spec(config.test_files), _spec(config.test_support_files), _spec(config.manifest_files),
                   _spec(config.exclude))

    def is_excluded(self, path: str) -> bool:
        """`.git/`, `.tddagents/` and the declared `exclude` patterns."""
        if any(segment in ALWAYS_EXCLUDED_DIRS for segment in path.split("/")):
            return True
        return bool(self.excluded.match_file(path))

    def is_test_file(self, path: str) -> bool:
        """
        A test file: one the declared `testFiles` patterns match, outside the excluded paths and not
        declared as test support. Support wins (user decision 2026-11-08): a file declared in both lists
        (`vitest.config.ts` beside `tests/`) is test configuration, and no runner names it in a report.
        """
        return (not self.is_excluded(path) and bool(self.test_files.match_file(path))
                and not self.test_support_files.match_file(path))

    def is_support_file(self, path: str) -> bool:
        """Test support (fixtures, helpers, test configuration): `testSupportFiles`, outside the excluded paths."""
        return not self.is_excluded(path) and bool(self.test_support_files.match_file(path))

    def is_test_code(self, path: str) -> bool:
        """What the phase tier and the fingerprint treat as tests: a test file or test support."""
        return self.is_test_file(path) or self.is_support_file(path)

    def is_manifest(self, path: str) -> bool:
        """A declared manifest or lockfile: writable in every phase, as any file is upstream."""
        return bool(self.manifest_files.match_file(path))


def project_relative_path(path: str, root: str | os.PathLike[str] | None) -> str | None:
    """
    `path` relative to the project root, in POSIX form, or `None` when it lies outside the project.
    A relative path is taken as relative to the root already.
    """
    expanded = os.path.expanduser(str(path).strip())
    if not expanded:
        return None
    if os.path.isabs(expanded):
        if root is None:
            return None
        relative = os.path.relpath(os.path.normpath(expanded), os.path.normpath(str(root)))
        if relative == ".." or relative.startswith("../"):
            relative = os.path.relpath(os.path.realpath(expanded), os.path.realpath(str(root)))
    else:
        relative = expanded
    normalized = posixpath.normpath(relative.replace(os.sep, "/"))
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


def _check_report_path(value: Any, key: str, issues: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        issues.append(f"`{key}` must be a non-empty path relative to the project root")
        return None
    normalized = posixpath.normpath(value.strip())
    if posixpath.isabs(normalized) or normalized == ".." or normalized.startswith("../"):
        issues.append(f"`{key}` must be a path inside the project, relative to its root (got {value!r})")
        return None
    return normalized


def _check_patterns(data: dict[str, Any], key: str, issues: list[str], *, required: bool) -> tuple[str, ...]:
    if key not in data:
        if required:
            issues.append(f"`{key}` is required: a list of gitignore patterns relative to the project root")
        return ()
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        issues.append(f"`{key}` must be a list of non-empty gitignore patterns")
        return ()
    if required and not value:
        issues.append(f"`{key}` must name at least one pattern")
        return ()
    patterns = tuple(item.strip() for item in value)
    try:
        _spec(patterns)
    except Exception as exc:  # noqa: BLE001 - pathspec raises several types for a bad pattern
        issues.append(f"`{key}` holds a pattern that is not a valid gitignore pattern: {exc}")
        return ()
    return patterns


#: The characters that make a gitignore path component a glob rather than a literal name.
_GLOB_CHARACTERS = frozenset("*?[\\")


def literal_prefix(pattern: str) -> str:
    """
    The leading components of a gitignore pattern that are literal names, as a root-relative path: `src/test/`
    is `src/test`, `tests/test_*.py` is `tests`, `**/dist/` and `*_test.go` are empty (no literal prefix).
    """
    components = pattern.strip().strip("/").split("/")
    literal: list[str] = []
    for component in components:
        if not component or _GLOB_CHARACTERS & set(component):
            break
        literal.append(component)
    return "/".join(literal)


def _overlaps(prefix: str, other: str) -> bool:
    """Whether one root-relative literal path is the other or lies inside it."""
    return bool(prefix and other) and (prefix == other or other.startswith(f"{prefix}/")
                                       or prefix.startswith(f"{other}/"))


def _check_exclude(exclude: tuple[str, ...], test_files: tuple[str, ...], issues: list[str]) -> None:
    """
    `exclude` accepts generated directories only: a pattern that does not end in `/`, or one whose literal
    prefix is, contains or lies inside a `testFiles` pattern's literal prefix, or that matches a `testFiles`
    pattern that is a literal path, is refused. Negated `testFiles` patterns name no test file.
    """
    declared = [pattern for pattern in test_files if not pattern.startswith("!")]
    offending: list[str] = []
    for pattern in exclude:
        if not pattern.endswith("/"):
            offending.append(pattern)
            continue
        if pattern.startswith("!"):
            continue  # a negation hides nothing
        prefix = literal_prefix(pattern)
        spec = _spec((pattern,))
        for test in declared:
            literal = literal_prefix(test)
            whole = literal == test.strip().strip("/")
            if _overlaps(prefix, literal) or (whole and literal and spec.match_file(
                    f"{literal}/" if test.strip().endswith("/") else literal)):
                offending.append(pattern)
                break
    if offending:
        issues.append("`exclude` accepts generated directories only (patterns ending in `/` that cover no "
                      f"`testFiles` pattern): {', '.join(offending)}")


def _check_command(data: dict[str, Any], key: str, issues: list[str], *, required: bool) -> str | None:
    if key not in data:
        if required:
            issues.append(f"`{key}` is required: the shell command that runs the whole test suite")
        return None
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        issues.append(f"`{key}` must be a non-empty shell command")
        return None
    return value.strip()


def _check_coverage(value: Any, issues: list[str]) -> CoverageReportConfig | None:
    if not isinstance(value, dict):
        issues.append("`coverageReport` must be an object with `format` and an optional `path`")
        return None
    unknown = sorted(set(value) - set(_COVERAGE_KEYS))
    if unknown:
        issues.append(f"`coverageReport` has unknown keys: {', '.join(unknown)}")
    fmt = value.get("format")
    if fmt not in COVERAGE_FORMATS:
        issues.append(f"`coverageReport.format` must be one of: {', '.join(COVERAGE_FORMATS)}")
    path = _check_report_path(value["path"], "coverageReport.path", issues) if "path" in value else None
    if fmt not in COVERAGE_FORMATS or unknown or ("path" in value and path is None):
        return None
    return CoverageReportConfig(format=str(fmt), path=path)


def parse_project_config(data: Any) -> tuple[ProjectConfig | None, tuple[str, ...]]:
    """
    The configuration from its decoded JSON, and every issue found. The configuration is `None`
    whenever there is an issue: a partly valid declaration is not used.
    """
    if not isinstance(data, dict):
        return None, ("the configuration must be a JSON object",)
    issues: list[str] = []
    unknown = sorted(set(data) - set(_KEYS))
    if unknown:
        issues.append(f"unknown keys: {', '.join(unknown)} (the keys are {', '.join(_KEYS)})")
    version = data.get("version")
    if version != PROJECT_CONFIG_VERSION or isinstance(version, bool):
        issues.append(f"`version` must be {PROJECT_CONFIG_VERSION}")
    test = _check_command(data, "test", issues, required=True)
    setup = _check_command(data, "setup", issues, required=False)
    list_tests = _check_command(data, "listTests", issues, required=False)
    junit = _check_report_path(data["junitReport"], "junitReport", issues) if "junitReport" in data else None
    coverage = _check_coverage(data["coverageReport"], issues) if "coverageReport" in data else None
    patterns = {key: _check_patterns(data, key, issues, required=key == "testFiles") for key in _PATTERN_KEYS}
    _check_exclude(patterns["exclude"], patterns["testFiles"], issues)
    if issues or test is None:
        return None, tuple(issues)
    return ProjectConfig(
        test=test, test_files=patterns["testFiles"], setup=setup, junit_report=junit, coverage_report=coverage,
        test_support_files=patterns["testSupportFiles"], manifest_files=patterns["manifestFiles"],
        exclude=patterns["exclude"], list_tests=list_tests,
    ), ()


def parse_project_config_text(text: str) -> tuple[ProjectConfig | None, tuple[str, ...]]:
    """`parse_project_config` over JSON text; a decoding error is the one issue."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, (f"the configuration is not valid JSON: {exc}",)
    return parse_project_config(data)


def load_project_config(workspace: Any) -> ProjectConfig | None:
    """
    The approved configuration in the workspace, or `None` when there is none. An unreadable or
    invalid file is logged and ignored, which leaves the TDD barriers failing closed.
    """
    reader = getattr(workspace, "read_file", None)
    if not callable(reader):
        return None
    try:
        text = str(reader(PROJECT_CONFIG_FILE))
    except Exception:  # noqa: BLE001 - no file (or no readable one) is no configuration
        return None
    config, issues = parse_project_config_text(text)
    if config is None:
        logger.warning("Ignoring invalid %s: %s", PROJECT_CONFIG_FILE, "; ".join(issues), extra=TERMINAL)
    return config


def invalid_project_config_note(issues: tuple[str, ...]) -> str:
    """
    What a fail-closed refusal adds when `project.json` exists but is invalid (2026-11-08): the file and
    its issues, so the model knows why there is no configuration. Empty when there are none.
    """
    if not issues:
        return ""
    return f" {PROJECT_CONFIG_FILE} is invalid, so it declares nothing: {'; '.join(issues)}."


def save_project_config(workspace: Any, config: ProjectConfig) -> None:
    """Write the configuration to `.tddagents/project.json` through the workspace."""
    workspace.write_file(PROJECT_CONFIG_FILE, config.to_json())


__all__ = [
    "ALWAYS_EXCLUDED_DIRS",
    "COVERAGE_FORMATS",
    "COVERAGE_REPORT_ENV",
    "DEFAULT_JUNIT_REPORT",
    "JUNIT_REPORT_ENV",
    "PROJECT_CONFIG_FILE",
    "PROJECT_CONFIG_SKILL",
    "PROJECT_CONFIG_VERSION",
    "REPORTS_DIR",
    "CoverageReportConfig",
    "ProjectConfig",
    "TestFileRules",
    "invalid_project_config_note",
    "literal_prefix",
    "load_project_config",
    "parse_project_config",
    "parse_project_config_text",
    "project_relative_path",
    "save_project_config",
]
