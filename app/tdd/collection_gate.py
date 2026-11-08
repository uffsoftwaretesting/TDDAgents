"""
Which declared test files a run ran, and which it did not (TDDAgents, user decisions 2026-10-28, 2026-11-08).

A live palindrome run wrote `tests/tests_trivial.py`, and its own `pytest.ini`
(`python_files = tests/test_*.py`) left it uncollected. RED still passed, because its evidence
was "the test files changed and RunTests ran", not "the new tests ran". A test file the runner
does not run checks nothing, like a skipped test, so it is treated like one.

Since 2026-11-03 the project declares which files are tests (`testFiles` in `.tddagents/project.json`,
`app/tdd/project_config.py`), and the files that ran are those its JUnit report names
(`app/tdd/junit_report.py`). Since 2026-11-08 the check is strict (user decision):

* **what a report names** (`resolve_ran`): a case names a file through its own or an enclosing suite's
  `file` attribute; else when its `classname` or an enclosing suite's `name` equals a file of the
  project's listing (vitest writes the path there). One case that names no file this way, and the
  report names no files;
* when the report names no files, **the project's `listTests` command** lists them, if it declares one
  (`app/tdd/test_listing.py`);
* **the files stay unknown** otherwise, which is a fact against the run in GREEN, REFACTOR and the final
  run, and is only stated in RED (`app/tdd/run_judgement.py`);
* a declared test file the run did not run (`uncollected`), and a file the run ran inside the project
  that the declaration does not cover (`undeclared`), each keep a run from a pass and from RED evidence.

Test support is never expected in a report. The check is made on every run that has a report, whatever its
exit code: a new test file that did not run gives no RED evidence either.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any

from app.tdd.junit_report import JUnitReport
from app.tdd.project_config import TestFileRules
from app.tdd.suite_fingerprint import project_files
from app.tdd.test_listing import TestListing

#: Why the files a run ran are unknown: the report names none, and no listing named them.
NO_FILES = "the JUnit report names no test files"
#: Why the declared test files that did not run are unknown although the run's files are known.
UNLISTABLE = "the project's files could not be listed"


@dataclass(frozen=True, slots=True)
class Collection:
    """What a run's report (or the project's listing) shows of the files it ran, against the declaration."""

    #: The files the run ran: those its report names, else those `listTests` listed; `None` when unknown.
    ran: frozenset[str] | None
    #: The declared test files the run did not run, sorted; `None` when that is unknown.
    uncollected: tuple[str, ...] | None
    #: The files the run ran inside the project that are neither test files nor test support, sorted.
    undeclared: tuple[str, ...] = ()
    #: The `listTests` run, when one ran.
    listing: TestListing | None = None

    @property
    def unknown(self) -> tuple[str, ...]:
        """Why `uncollected` is unknown, stated as facts; empty when it is known."""
        if self.uncollected is not None:
            return ()
        if self.ran is not None:
            return (UNLISTABLE,)
        listing_fact = self.listing.fact if self.listing is not None else None
        return (NO_FILES,) if listing_fact is None else (NO_FILES, listing_fact)


def project_test_files(workspace: Any, rules: TestFileRules) -> frozenset[str] | None:
    """
    The project's test files: the files its `testFiles` patterns match, outside the excluded paths.
    Test support (`testSupportFiles`: fixtures, helpers, test configuration) is not expected in a report,
    even when a `testFiles` pattern also matches it (`TestFileRules.is_test_file`).
    `None` when the workspace cannot be listed.
    """
    files = project_files(workspace, rules)
    if files is None:
        return None
    return frozenset(path for path in files if rules.is_test_file(path))


def resolve_ran(report: JUnitReport, listed: frozenset[str] | None, rules: TestFileRules) -> frozenset[str] | None:
    """
    The files the report names (see the module docstring), or `None` when one case names none. A case
    without a `file` attribute names the first of its `classname` and suite names that is a file of
    `listed` (the project's listing) outside the excluded paths; with no listing it names none.
    """
    if not report.unnamed:
        return report.files
    if listed is None:
        return None
    ran = set(report.named)
    for candidates in report.unnamed:
        named = next((name for name in candidates if name in listed and not rules.is_excluded(name)), None)
        if named is None:
            return None
        ran.add(named)
    return frozenset(ran)


def _inside_project(path: str) -> bool:
    return not posixpath.isabs(path) and path != ".." and not path.startswith("../")


def observe_collection(workspace: Any, rules: TestFileRules | None, report: JUnitReport | None,
                       listing: TestListing | None = None) -> Collection | None:
    """
    The run's collection, against the declaration; `None` when there is nothing to observe it from: no
    declaration (`rules`), no report, or a report that records no tests.
    """
    if rules is None or report is None or not report.tests:
        return None
    files = project_files(workspace, rules)
    listed = frozenset(files) if files is not None else None
    ran = resolve_ran(report, listed, rules)
    if ran is None and listing is not None and listing.files is not None:
        ran = listing.files
    if ran is None:
        return Collection(None, None, (), listing)
    undeclared = tuple(sorted(path for path in ran if _inside_project(path) and not rules.is_test_code(path)))
    if listed is None:
        return Collection(ran, None, undeclared, listing)
    expected = {path for path in listed if rules.is_test_file(path)}
    return Collection(ran, tuple(sorted(expected - ran)), undeclared, listing)


__all__ = ["NO_FILES", "UNLISTABLE", "Collection", "observe_collection", "project_test_files", "resolve_ran"]
