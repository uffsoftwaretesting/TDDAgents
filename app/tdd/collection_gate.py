"""
Which test files a RunTests run actually ran (TDDAgents, user decision 2026-10-28).

A live palindrome run wrote `tests/tests_trivial.py`, and its own `pytest.ini`
(`python_files = tests/test_*.py`) left it uncollected. RED still passed, because its evidence
was "the test files changed and RunTests ran", not "the new tests ran". A test file pytest
does not collect checks nothing, like a skipped test, so it is treated like one:

* the run's collected files come from pytest's JUnit XML report (`xunit1`, which names each
  test case's `file`), written into the workspace's protected `.tddagents/coverage/` and read
  back through the `Workspace` protocol, so the model's verbosity or config cannot hide them;
* the project's test modules are the Python files `is_test_path` classifies as tests that
  define a pytest test (`def test…`, `class Test…`), without `__init__.py` and `conftest.py`,
  and within the run's `test_path`;
* a module in neither list is uncollected. `run_tests_tool` then refuses RED's evidence and
  a GREEN or REFACTOR pass, and the session's final run reports it as not passing;
* the same report records the files pytest could not collect (`collection_error_files`): the
  structured evidence that an exit-2 run stopped on collection errors.
"""

from __future__ import annotations

import posixpath
from typing import Any
from xml.etree import ElementTree

from app.tdd.coverage_gate import COVERAGE_DIR
from app.tdd.project_config import TestFileRules
from app.tdd.suite_fingerprint import project_files

JUNIT_REPORT_PATH = f"{COVERAGE_DIR}/junit.xml"


def junit_options() -> str:
    """The pytest options that write the report this module reads."""
    return f"--junitxml={JUNIT_REPORT_PATH} -o junit_family=xunit1"


def collected_files(report: str | None) -> frozenset[str] | None:
    """The files of the report's test cases, or `None` when there is no readable report."""
    if not report:
        return None
    try:
        root = ElementTree.fromstring(report)
    except ElementTree.ParseError:
        return None
    files = {posixpath.normpath(str(case.get("file"))) for case in root.iter("testcase") if case.get("file")}
    return frozenset(files)


def collection_error_files(report: str | None) -> frozenset[str] | None:
    """
    The test files pytest recorded collection errors for: in its JUnit report, a test case with an
    `<error message="collection failure">`, naming the file. `None` when there is no readable report.
    The error's own text is never read (2026-10-28): RunTests reports what pytest recorded, not why.
    """
    if not report:
        return None
    try:
        root = ElementTree.fromstring(report)
    except ElementTree.ParseError:
        return None
    files = set()
    for case in root.iter("testcase"):
        error = case.find("error")
        if case.get("file") and error is not None and error.get("message") == "collection failure":
            files.add(posixpath.normpath(str(case.get("file"))))
    return frozenset(files)


def skipped_tests(report: str | None) -> int | None:
    """The skipped tests the report's test suites record (`skipped="N"`), or `None` with no readable report."""
    if not report:
        return None
    try:
        root = ElementTree.fromstring(report)
    except ElementTree.ParseError:
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = 0
    for suite in suites:
        try:
            total += int(suite.get("skipped") or 0)
        except ValueError:
            return None
    return total


def project_test_files(workspace: Any, rules: TestFileRules) -> frozenset[str] | None:
    """
    The project's test files: the files its `testFiles` patterns match, without its test-support
    files (`testSupportFiles`: a runner never reports those as tests), outside the excluded paths.
    `None` when the workspace cannot be listed.
    """
    files = project_files(workspace, rules)
    if files is None:
        return None
    return frozenset(path for path in files if rules.is_test_file(path) and not rules.is_support_file(path))


def uncollected_test_files(workspace: Any, rules: TestFileRules | None,
                           collected: frozenset[str] | None) -> tuple[str, ...]:
    """The declared test files the run's report does not name; none when either side is unknown."""
    if collected is None or rules is None:
        return ()
    files = project_test_files(workspace, rules)
    if files is None:
        return ()
    return tuple(sorted(files - collected))


__all__ = [
    "JUNIT_REPORT_PATH",
    "collected_files",
    "collection_error_files",
    "junit_options",
    "project_test_files",
    "skipped_tests",
    "uncollected_test_files",
]
