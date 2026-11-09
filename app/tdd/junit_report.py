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
* for a case that names no file that way, the names that may still be its file (2026-11-08): its
  `classname`, then the `name` of each enclosing `<testsuite>`, innermost first (`unnamed`), each
  normalised as a file is. vitest's `--reporter=junit` writes the file's path there. Whether one of
  them is a file of the project is decided against the project's listing
  (`app/tdd/collection_gate.py::resolve_ran`), never here;
* each failed or errored case, in document order (user decision 2026-11-08): its `classname` and
  `name`, its file, and the first line of its `<failure>` / `<error>` `message` attribute (else of
  the element's text), verbatim. A runner whose JUnit reporter replaces its console output (vitest's
  `--reporter=junit`) otherwise leaves no trace of which test failed.

A namespace on the tags is ignored. An empty, unreadable or non-JUnit file is no report (`None`).
"""

from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass, field
from xml.etree import ElementTree

from app.tdd.project_config import project_relative_path


@dataclass(frozen=True, slots=True)
class FailedCase:
    """A failed or errored `<testcase>`: what the report records about it, verbatim."""

    outcome: str  # "failure" or "error", the child element's tag
    classname: str
    name: str
    file: str | None = None
    message: str | None = None
    __test__ = False

    @property
    def label(self) -> str:
        """`classname > name`, either alone when the other is empty or repeats it."""
        parts = [part for part in (self.classname, self.name) if part]
        if len(parts) == 2 and parts[0] == parts[1]:
            parts = parts[:1]
        return " > ".join(parts)


@dataclass(frozen=True, slots=True)
class JUnitReport:
    """The test cases a JUnit report records, by outcome, and the files they ran from (`None`: unknown)."""

    tests: int
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    files: frozenset[str] | None = None
    #: The failed and errored cases, in document order.
    failed_cases: tuple[FailedCase, ...] = ()
    #: The files the cases' `file` attributes name, even when some case names none (`files` is then `None`).
    named: frozenset[str] = field(default=frozenset(), compare=False)
    #: The cases each of those files' `file` attributes name: `(path, cases)` pairs, sorted by path.
    named_counts: tuple[tuple[str, int], ...] = field(default=(), compare=False)
    #: For each case that names no file through a `file` attribute, in document order: the names that may
    #: be its file (its `classname`, then its enclosing suites' names, innermost first), normalised.
    unnamed: tuple[tuple[str, ...], ...] = field(default=(), compare=False)
    __test__ = False

    @property
    def passed(self) -> int:
        return self.tests - self.failures - self.errors - self.skipped


def _tag(element: ElementTree.Element) -> str:
    return str(element.tag).rsplit("}", 1)[-1]


def _child_tags(element: ElementTree.Element) -> set[str]:
    return {_tag(child) for child in element}


def _first_line(text: str | None) -> str | None:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return None


def _failed_case(case: ElementTree.Element, outcome: str, file: str | None) -> FailedCase:
    child = next(child for child in case if _tag(child) == outcome)
    return FailedCase(outcome=outcome, classname=(case.get("classname") or "").strip(),
                      name=(case.get("name") or "").strip(), file=file,
                      message=_first_line(child.get("message")) or _first_line(child.text))


def _normalize_file(name: str, root: str | os.PathLike[str] | None) -> str:
    """A case's file, relative to the project root when it lies inside it, in POSIX form."""
    cleaned = name.strip().replace("\\", "/")
    if posixpath.isabs(cleaned):
        relative = project_relative_path(cleaned, root)
        return relative if relative is not None else posixpath.normpath(cleaned)
    return posixpath.normpath(cleaned)


def _candidates(classname: str | None, suites: tuple[str, ...],
                root: str | os.PathLike[str] | None) -> tuple[str, ...]:
    """A case's `classname`, then its suites' names, normalised as files are, blanks and repeats left out."""
    names: list[str] = []
    for name in (classname or "", *suites):
        if name.strip():
            normalized = _normalize_file(name, root)
            if normalized not in names:
                names.append(normalized)
    return tuple(names)


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
    per_file: dict[str, int] = {}
    failed: list[FailedCase] = []
    unnamed: list[tuple[str, ...]] = []
    pending: list[tuple[ElementTree.Element, str | None, tuple[str, ...]]] = [(document, None, ())]
    while pending:
        element, inherited, suites = pending.pop()
        tag = _tag(element)
        if tag == "testcase":
            counts["tests"] += 1
            outcome = next((kind for kind in ("failure", "error", "skipped") if kind in _child_tags(element)), None)
            name = element.get("file") or inherited
            file = _normalize_file(name, root) if name and name.strip() else None
            if outcome is not None:
                counts[outcome] += 1
                if outcome != "skipped":
                    failed.append(_failed_case(element, outcome, file))
            if file is not None:
                files.add(file)
                per_file[file] = per_file.get(file, 0) + 1
            else:
                unnamed.append(_candidates(element.get("classname"), suites, root))
            continue
        here = element.get("file") if tag == "testsuite" else None
        # The enclosing suites' names, innermost first.
        names = (element.get("name") or "", *suites) if tag == "testsuite" else suites
        # Reversed onto the stack, so the cases are visited in document order.
        pending.extend((child, here or inherited, names) for child in reversed(element))
    return JUnitReport(
        tests=counts["tests"], failures=counts["failure"], errors=counts["error"], skipped=counts["skipped"],
        files=None if unnamed or not files else frozenset(files), failed_cases=tuple(failed),
        named=frozenset(files), named_counts=tuple(sorted(per_file.items())), unnamed=tuple(unnamed),
    )


__all__ = ["FailedCase", "JUnitReport", "parse_junit_report"]
