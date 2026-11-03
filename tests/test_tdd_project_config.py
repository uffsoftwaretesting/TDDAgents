"""
The project's declared configuration (`app/tdd/project_config.py`, 2026-11-03): the strict schema,
every issue at once, the gitignore matchers, the plan-file section and the file in `.tddagents/`.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from app.tdd.plan import PROJECT_CONFIG_HEADING, parse_project_configuration
from app.tdd.project_config import (
    COVERAGE_REPORT_ENV,
    DEFAULT_JUNIT_REPORT,
    JUNIT_REPORT_ENV,
    PROJECT_CONFIG_FILE,
    REPORTS_DIR,
    CoverageReportConfig,
    ProjectConfig,
    load_project_config,
    parse_project_config,
    parse_project_config_text,
    project_relative_path,
    save_project_config,
)
from tests.conftest import FakeWorkspace

FULL: dict[str, Any] = {
    "version": 1,
    "setup": "npm ci",
    "test": "npx vitest run",
    "junitReport": "reports/junit.xml",
    "coverageReport": {"path": "coverage/lcov.info", "format": "lcov"},
    "testFiles": ["**/*.test.ts"],
    "testSupportFiles": ["test/helpers/"],
    "manifestFiles": ["package.json", "package-lock.json"],
    "exclude": ["node_modules/", "dist/"],
}


def test_the_constants_are_the_declared_ones():
    assert (PROJECT_CONFIG_FILE, REPORTS_DIR, DEFAULT_JUNIT_REPORT) == (
        ".tddagents/project.json", ".tddagents/reports", ".tddagents/reports/junit.xml")
    assert (JUNIT_REPORT_ENV, COVERAGE_REPORT_ENV) == ("TDDAGENTS_JUNIT_REPORT", "TDDAGENTS_COVERAGE_REPORT")
    assert PROJECT_CONFIG_HEADING == "Project Configuration"


def test_a_full_configuration_parses_and_round_trips():
    config, issues = parse_project_config(FULL)
    assert issues == ()
    assert config == ProjectConfig(
        test="npx vitest run", test_files=("**/*.test.ts",), setup="npm ci", junit_report="reports/junit.xml",
        coverage_report=CoverageReportConfig(format="lcov", path="coverage/lcov.info"),
        test_support_files=("test/helpers/",), manifest_files=("package.json", "package-lock.json"),
        exclude=("node_modules/", "dist/"))
    assert config.to_dict() == FULL
    assert parse_project_config_text(config.to_json()) == (config, ())
    assert config.junit_report_path == "reports/junit.xml"
    assert config.coverage_report is not None and config.coverage_report.report_path == "coverage/lcov.info"


def test_a_minimal_configuration_takes_the_defaults():
    config, issues = parse_project_config({"version": 1, "test": "  go test ./...  ", "testFiles": ["*_test.go"]})
    assert issues == () and config is not None
    assert (config.test, config.setup, config.junit_report, config.coverage_report) == (
        "go test ./...", None, None, None)
    assert config.junit_report_path == DEFAULT_JUNIT_REPORT
    assert config.to_dict() == {"version": 1, "test": "go test ./...", "testFiles": ["*_test.go"],
                                "testSupportFiles": [], "manifestFiles": [], "exclude": []}
    assert CoverageReportConfig(format="cobertura").report_path == ".tddagents/reports/coverage.xml"
    assert CoverageReportConfig(format="lcov").report_path == ".tddagents/reports/lcov.info"


@pytest.mark.parametrize(("data", "issues"), [
    ([], ("the configuration must be a JSON object",)),
    ({}, ("`version` must be 1", "`test` is required: the shell command that runs the whole test suite",
          "`testFiles` is required: a list of gitignore patterns relative to the project root")),
    ({**FULL, "version": True}, ("`version` must be 1",)),
    ({**FULL, "version": 2}, ("`version` must be 1",)),
    ({**FULL, "extra": 1, "Test": 2}, (
        "unknown keys: Test, extra (the keys are version, setup, test, junitReport, coverageReport, testFiles, "
        "testSupportFiles, manifestFiles, exclude)",)),
    ({**FULL, "test": " "}, ("`test` must be a non-empty shell command",)),
    ({**FULL, "setup": 3}, ("`setup` must be a non-empty shell command",)),
    ({**FULL, "junitReport": "../out.xml"}, (
        "`junitReport` must be a path inside the project, relative to its root (got '../out.xml')",)),
    ({**FULL, "junitReport": "/tmp/out.xml"}, (
        "`junitReport` must be a path inside the project, relative to its root (got '/tmp/out.xml')",)),
    ({**FULL, "junitReport": ""}, ("`junitReport` must be a non-empty path relative to the project root",)),
    ({**FULL, "coverageReport": "lcov"}, ("`coverageReport` must be an object with `format` and an optional `path`",)),
    ({**FULL, "coverageReport": {"format": "jacoco"}}, ("`coverageReport.format` must be one of: cobertura, lcov",)),
    ({**FULL, "coverageReport": {"format": "lcov", "file": "x"}}, ("`coverageReport` has unknown keys: file",)),
    ({**FULL, "coverageReport": {"format": "lcov", "path": ".."}}, (
        "`coverageReport.path` must be a path inside the project, relative to its root (got '..')",)),
    ({**FULL, "testFiles": []}, ("`testFiles` must name at least one pattern",)),
    ({**FULL, "testFiles": "tests/"}, ("`testFiles` must be a list of non-empty gitignore patterns",)),
    ({**FULL, "exclude": ["ok", ""]}, ("`exclude` must be a list of non-empty gitignore patterns",)),
    ({**FULL, "manifestFiles": [1]}, ("`manifestFiles` must be a list of non-empty gitignore patterns",)),
])
def test_every_issue_is_reported_and_nothing_is_used(data: Any, issues: tuple[str, ...]):
    assert parse_project_config(data) == (None, issues)


def test_optional_lists_may_be_left_out_and_paths_are_normalized():
    config, _ = parse_project_config({"version": 1, "test": "t", "testFiles": ["a"], "junitReport": "./r//j.xml",
                                      "coverageReport": {"format": "cobertura", "path": "c/./x.xml"}})
    assert config is not None and config.junit_report == "r/j.xml"
    assert config.coverage_report == CoverageReportConfig(format="cobertura", path="c/x.xml")


def test_invalid_json_is_one_issue():
    config, issues = parse_project_config_text("{nope")
    assert config is None and len(issues) == 1 and issues[0].startswith("the configuration is not valid JSON: ")


# ── the matchers ────────────────────────────────────────────────────────────


RULES = ProjectConfig(
    test="t", test_files=("tests/**/test_*.py", "*_test.go"), test_support_files=("conftest.py", "tests/data/"),
    manifest_files=("pyproject.toml", "go.sum"), exclude=("node_modules/", ".venv/")).rules


@pytest.mark.parametrize(("path", "test", "support", "manifest", "excluded"), [
    ("tests/test_a.py", True, False, False, False),
    ("tests/sub/test_b.py", True, False, False, False),
    ("tests/helper.py", False, False, False, False),
    ("pkg/a_test.go", True, False, False, False),
    ("conftest.py", False, True, False, False),
    ("tests/conftest.py", False, True, False, False),
    ("tests/data/x.json", False, True, False, False),
    ("pyproject.toml", False, False, True, False),
    ("sub/go.sum", False, False, True, False),
    ("node_modules/x/test/y_test.go", False, False, False, True),
    (".venv/lib/tests/test_x.py", False, False, False, True),
    (".git/tests/test_x.py", False, False, False, True),
    (".tddagents/tests/test_x.py", False, False, False, True),
    ("a/.git", False, False, False, True),
    ("src/app.py", False, False, False, False),
])
def test_the_gitignore_matchers(path: str, test: bool, support: bool, manifest: bool, excluded: bool):
    assert (RULES.is_test_file(path), RULES.is_support_file(path), RULES.is_manifest(path),
            RULES.is_excluded(path)) == (test, support, manifest, excluded)
    assert RULES.is_test_code(path) == (test or support)


def test_the_rules_are_built_once_per_configuration():
    config = ProjectConfig(test="t", test_files=("x",))
    assert config.rules is config.rules
    assert config == ProjectConfig(test="t", test_files=("x",))
    # A replaced configuration builds its own matchers, never its source's.
    other = dataclasses.replace(config, test_files=("y",))
    assert other.rules.is_test_file("y") and not other.rules.is_test_file("x")


@pytest.mark.parametrize(("path", "root", "expected"), [
    ("tests/a.py", "/p", "tests/a.py"),
    ("./tests//a.py", "/p", "tests/a.py"),
    ("/p/tests/a.py", "/p", "tests/a.py"),
    ("/p", "/p", "."),
    ("/elsewhere/a.py", "/p", None),
    ("../a.py", "/p", None),
    ("/p/a.py", None, None),
    ("  ", "/p", None),
    ("", "/p", None),
])
def test_project_relative_paths(path: str, root: str | None, expected: str | None):
    assert project_relative_path(path, root) == expected


def test_a_symlinked_root_is_followed(tmp_path: Path):
    real = tmp_path / "real"
    (real / "tests").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    assert project_relative_path(str(real / "tests" / "a.py"), str(link)) == "tests/a.py"


# ── the plan section ────────────────────────────────────────────────────────


def _plan(section: str) -> str:
    return f"# Plan\n\n## Implementation Plan\n1. a\n\n{section}\n## Verification\nRun it.\n"


def test_the_section_holds_one_json_block():
    block = json.dumps({"version": 1, "test": "make test", "testFiles": ["t/"]})
    config, issues = parse_project_configuration(_plan(f"## Project Configuration\nWhy.\n```json\n{block}\n```\n"))
    assert issues == () and config == ProjectConfig(test="make test", test_files=("t/",))


def test_any_heading_level_and_case_and_fences_hide_headings():
    block = '{"version": 1, "test": "t", "testFiles": ["x"]}'
    doc = f"# Plan\n### project configuration\n````json\n{block}\n````\n~~~\n# not a heading\n~~~\n"
    assert parse_project_configuration(doc)[1] == ()


@pytest.mark.parametrize(("doc", "issue"), [
    ("# Plan\n", 'the plan has no "## Project Configuration" section'),
    ("```\n## Project Configuration\n```\n", 'the plan has no "## Project Configuration" section'),
    (_plan("## Project Configuration\n```\n{}\n```\n"),
     'the "Project Configuration" section must hold exactly one fenced json block (found none)'),
    (_plan("## Project Configuration\n```json\n{}\n```\n```json\n{}\n```\n"),
     'the "Project Configuration" section must hold exactly one fenced json block (found 2)'),
])
def test_a_missing_or_ambiguous_section_is_an_issue(doc: str, issue: str):
    assert parse_project_configuration(doc) == (None, (issue,))


def test_a_block_after_the_section_is_not_read():
    doc = '## Project Configuration\nnone\n## Other\n```json\n{"version": 1}\n```\n'
    assert parse_project_configuration(doc)[1] == (
        'the "Project Configuration" section must hold exactly one fenced json block (found none)',)


def test_a_deeper_heading_stays_in_the_section():
    doc = ('## Project Configuration\n### Details\n```json\n{"version": 1, "test": "t", "testFiles": ["x"]}\n```\n'
           "# Next\n")
    assert parse_project_configuration(doc)[1] == ()


def test_the_section_issues_come_from_the_schema():
    doc = _plan('## Project Configuration\n```json\n{"version": 1}\n```\n')
    assert parse_project_configuration(doc) == (None, (
        "`test` is required: the shell command that runs the whole test suite",
        "`testFiles` is required: a list of gitignore patterns relative to the project root"))


# ── the file ────────────────────────────────────────────────────────────────


def test_save_and_load_through_the_workspace():
    ws = FakeWorkspace()
    config = ProjectConfig(test="t", test_files=("x",), setup="s")
    save_project_config(ws, config)
    assert json.loads(ws.files[PROJECT_CONFIG_FILE]) == config.to_dict()
    assert load_project_config(ws) == config


def test_no_file_an_invalid_file_and_no_workspace_load_nothing(caplog: pytest.LogCaptureFixture):
    assert load_project_config(FakeWorkspace()) is None
    assert load_project_config(None) is None
    ws = FakeWorkspace(files={PROJECT_CONFIG_FILE: '{"version": 1}'})
    with caplog.at_level("WARNING"):
        assert load_project_config(ws) is None
    assert "Ignoring invalid .tddagents/project.json" in caplog.text
