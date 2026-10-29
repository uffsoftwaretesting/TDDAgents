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
import re
from typing import Any
from xml.etree import ElementTree

from app.tdd.coverage_gate import COVERAGE_DIR
from app.tdd.phase_permissions import is_test_path
from app.tdd.suite_fingerprint import IGNORED_SEGMENTS, LISTING_DEPTH

JUNIT_REPORT_PATH = f"{COVERAGE_DIR}/junit.xml"

#: pytest's default test discovery: `test` functions (also async) and `Test` classes.
_DEFINES_A_TEST = re.compile(r"^\s*(?:async\s+)?def\s+test|^\s*class\s+Test", re.MULTILINE)

#: Test-support files pytest never collects as test modules.
_SUPPORT_FILES = frozenset({"__init__.py", "conftest.py"})


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


def _in_scope(path: str, test_path: str) -> bool:
    scope = posixpath.normpath(test_path.split("::", 1)[0].strip() or ".")
    return scope == "." or path == scope or path.startswith(scope.rstrip("/") + "/")


def project_test_modules(workspace: Any, test_path: str = ".") -> frozenset[str] | None:
    """The project's test modules within `test_path`, or `None` when the workspace cannot be listed."""
    lister = getattr(workspace, "list_files", None)
    if not callable(lister):
        return None
    try:
        entries = lister(".", depth=LISTING_DEPTH)
    except Exception:  # noqa: BLE001 - an unlistable workspace gives no evidence either way
        return None
    modules = set()
    for entry in entries:
        path = posixpath.normpath(str(entry.path))
        segments = path.split("/")
        if (entry.is_dir or not path.endswith(".py") or segments[-1] in _SUPPORT_FILES
                or any(segment in IGNORED_SEGMENTS for segment in segments)
                or not is_test_path(path) or not _in_scope(path, test_path)):
            continue
        try:
            content = str(workspace.read_file(path))
        except Exception:  # noqa: BLE001, S112 - an unreadable file defines no test we can see
            continue
        if _DEFINES_A_TEST.search(content):
            modules.add(path)
    return frozenset(modules)


def uncollected_test_files(workspace: Any, test_path: str, collected: frozenset[str] | None) -> tuple[str, ...]:
    """The test modules in scope pytest did not collect; none when either side is unknown."""
    if collected is None:
        return ()
    modules = project_test_modules(workspace, test_path)
    if modules is None:
        return ()
    return tuple(sorted(modules - collected))


__all__ = [
    "JUNIT_REPORT_PATH",
    "collected_files",
    "collection_error_files",
    "junit_options",
    "project_test_modules",
    "skipped_tests",
    "uncollected_test_files",
]
