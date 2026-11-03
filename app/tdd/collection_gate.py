"""
Which declared test files a run did not run (TDDAgents, user decision 2026-10-28).

A live palindrome run wrote `tests/tests_trivial.py`, and its own `pytest.ini`
(`python_files = tests/test_*.py`) left it uncollected. RED still passed, because its evidence
was "the test files changed and RunTests ran", not "the new tests ran". A test file the runner
does not run checks nothing, like a skipped test, so it is treated like one.

Since 2026-11-03 the project declares which files are tests (`testFiles` in `.tddagents/project.json`,
`app/tdd/project_config.py`), and the files that ran are those its JUnit report names
(`app/tdd/junit_report.py`). A declared test file the report does not name did not run; test
support is never expected in a report. When the report names no files that is unknown, and nothing
is held against the run (`app/tdd/run_judgement.py`).
"""

from __future__ import annotations

from typing import Any

from app.tdd.project_config import TestFileRules
from app.tdd.suite_fingerprint import project_files


def project_test_files(workspace: Any, rules: TestFileRules) -> frozenset[str] | None:
    """
    The project's test files: the files its `testFiles` patterns match, outside the excluded paths.
    Test support (`testSupportFiles`: fixtures, helpers, test configuration) is not expected in a report.
    `None` when the workspace cannot be listed.
    """
    files = project_files(workspace, rules)
    if files is None:
        return None
    return frozenset(path for path in files if rules.is_test_file(path))


def uncollected_test_files(workspace: Any, rules: TestFileRules | None,
                           ran: frozenset[str] | None) -> tuple[str, ...] | None:
    """The declared test files the report does not name; `None` when either side is unknown."""
    if ran is None or rules is None:
        return None
    files = project_test_files(workspace, rules)
    if files is None:
        return None
    return tuple(sorted(files - ran))


__all__ = ["project_test_files", "uncollected_test_files"]
