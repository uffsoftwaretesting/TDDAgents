"""
The JUnit XML report a test command writes: what RunTests reads beside the exit code (2026-11-03).

JUnit XML is the one report format nearly every ecosystem's runner writes: pytest (`--junitxml`),
jest (`jest-junit`), vitest (`--reporter=junit`), Maven surefire and Gradle, `go-junit-report` or
`gotestsum`, cargo-nextest, PHPUnit (`--log-junit`), RSpec and .NET loggers. Their shapes differ in
the details, so only what they share is read, structurally and never from any message text:

* every `<testcase>`, at any depth (a `<testsuites>` root, a bare `<testsuite>`, nested suites);
* a case is failed, errored or skipped by its `<failure>`, `<error>` or `<skipped>` child (the
  suite-level counters are not trusted: several runners leave them out or count differently);
* the file a case ran from: its own `file` attribute, else the nearest enclosing `<testsuite>`'s.
  `files` is `None` (unknown) unless every case names one: many runners (surefire, go, nextest)
  name none, and a partial list would make every unnamed file look as if it never ran.

A namespace on the tags is ignored. An empty, unreadable or non-JUnit file is no report (`None`).
"""

from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass
from xml.etree import ElementTree

from app.tdd.project_config import project_relative_path


@dataclass(frozen=True, slots=True)
class JUnitReport:
    """The test cases a JUnit report records, by outcome, and the files they ran from (`None`: unknown)."""

    tests: int
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    files: frozenset[str] | None = None
    __test__ = False

    @property
    def passed(self) -> int:
        return self.tests - self.failures - self.errors - self.skipped


def _tag(element: ElementTree.Element) -> str:
    return str(element.tag).rsplit("}", 1)[-1]


def _child_tags(element: ElementTree.Element) -> set[str]:
    return {_tag(child) for child in element}


def _normalize_file(name: str, root: str | os.PathLike[str] | None) -> str:
    """A case's file, relative to the project root when it lies inside it, in POSIX form."""
    cleaned = name.strip().replace("\\", "/")
    if posixpath.isabs(cleaned):
        relative = project_relative_path(cleaned, root)
        return relative if relative is not None else posixpath.normpath(cleaned)
    return posixpath.normpath(cleaned)


def parse_junit_report(text: str | None, root: str | os.PathLike[str] | None = None) -> JUnitReport | None:
    """The report's cases (see the module docstring), or `None` when there is no readable JUnit report."""
    if not text or not text.strip():
        return None
    try:
        document = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
    if _tag(document) not in ("testsuites", "testsuite", "testcase"):
        return None
    counts = {"tests": 0, "failure": 0, "error": 0, "skipped": 0}
    files: set[str] = set()
    unnamed = False
    pending: list[tuple[ElementTree.Element, str | None]] = [(document, None)]
    while pending:
        element, inherited = pending.pop()
        tag = _tag(element)
        if tag == "testcase":
            counts["tests"] += 1
            outcome = next((kind for kind in ("failure", "error", "skipped") if kind in _child_tags(element)), None)
            if outcome is not None:
                counts[outcome] += 1
            name = element.get("file") or inherited
            if name and name.strip():
                files.add(_normalize_file(name, root))
            else:
                unnamed = True
            continue
        here = element.get("file") if tag == "testsuite" else None
        pending.extend((child, here or inherited) for child in element)
    return JUnitReport(
        tests=counts["tests"], failures=counts["failure"], errors=counts["error"], skipped=counts["skipped"],
        files=None if unnamed or not files else frozenset(files),
    )


__all__ = ["JUnitReport", "parse_junit_report"]
