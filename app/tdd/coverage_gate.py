"""
The coverage every RunTests run reports (Phase 12), read from the report the test command writes.

Since 2026-11-03 the project declares how its tests run (`app/tdd/project_config.py`), and with
`coverageReport` the test command writes a coverage report to `$TDDAGENTS_COVERAGE_REPORT` in one of
the two formats nearly every ecosystem can produce:

* **cobertura** XML (coverage.py `--cov-report=xml`, istanbul/c8 `cobertura`, coverlet, JaCoCo and
  gcovr converters): each `<class filename=…>`'s `<line number hits branch condition-coverage>`;
* **lcov** tracefiles (istanbul/c8/vitest `lcov`, `cargo llvm-cov --lcov`, `go-test-coverage`, simplecov
  lcov): each `SF:` record's `DA:` lines and `BRDA:` branches.

The figure is **line + branch** over the production code, as coverage.py's total: covered lines plus
covered branches over all lines plus all branches. A file the project declares as a test, test
support or excluded is left out, so a runner that also measures its tests does not inflate it.
A cobertura report with no per-file data falls back to its root totals.

Coverage is guidance, not a gate (user decision, 2026-10-28): REFACTOR, still mandatory, ends on a
passing run, and raising coverage is part of its work, with `COVERAGE_RULE_OF_THUMB` as a good rule
of thumb. A report that measured nothing is `NO_CODE`; no report, an unreadable one or no declared
report is `UNAVAILABLE`.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from xml.etree import ElementTree

from app.tdd.project_config import project_relative_path

#: Line + branch coverage of the production code that is a good rule of thumb (user decision).
COVERAGE_RULE_OF_THUMB = 90.0


class CoverageStatus(StrEnum):
    MEASURED = "measured"
    NO_CODE = "no_code"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CoverageReading:
    status: CoverageStatus
    percent: float | None = None

    def describe(self) -> str:
        if self.status == CoverageStatus.MEASURED:
            return f"coverage {self.percent:.1f}% (rule of thumb: {COVERAGE_RULE_OF_THUMB:.0f}%)"
        if self.status == CoverageStatus.NO_CODE:
            return "coverage: no production code to measure"
        return "coverage could not be measured"


UNAVAILABLE = CoverageReading(CoverageStatus.UNAVAILABLE)
NO_CODE = CoverageReading(CoverageStatus.NO_CODE)

#: Whether a file the report names is production code (its path as the report gives it).
FileFilter = Callable[[str], bool]

_CONDITION = re.compile(r"\((\d+)/(\d+)\)")


@dataclass
class _Tally:
    lines: int = 0
    lines_hit: int = 0
    branches: int = 0
    branches_hit: int = 0

    def reading(self) -> CoverageReading:
        total = self.lines + self.branches
        if total == 0:
            return NO_CODE
        return CoverageReading(CoverageStatus.MEASURED, 100.0 * (self.lines_hit + self.branches_hit) / total)


def _int(value: str | None) -> int:
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


def _read_cobertura(text: str, include: FileFilter) -> CoverageReading:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return UNAVAILABLE
    if str(root.tag).rsplit("}", 1)[-1] != "coverage":
        return UNAVAILABLE
    sources = [str(s.text).strip() for s in root.iter("source") if s.text and s.text.strip()]
    classes = list(root.iter("class"))
    if not classes:
        attributes = ("lines-valid", "lines-covered", "branches-valid", "branches-covered")
        if not all(root.get(key) is not None for key in attributes):
            return UNAVAILABLE
        valid, covered, branches, branches_covered = (_int(root.get(key)) for key in attributes)
        return _Tally(valid, covered, branches, branches_covered).reading()
    tally = _Tally()
    seen: set[tuple[str, str]] = set()
    for cls in classes:
        filename = str(cls.get("filename") or "")
        candidates = [filename, *(posixpath.join(source, filename) for source in sources)]
        if not filename or not all(include(candidate) for candidate in candidates):
            continue
        for line in cls.iter("line"):
            key = (filename, str(line.get("number")))
            if key in seen:
                continue
            seen.add(key)
            tally.lines += 1
            tally.lines_hit += _int(line.get("hits")) > 0
            condition = _CONDITION.search(str(line.get("condition-coverage") or ""))
            if line.get("branch") == "true" and condition:
                tally.branches_hit += int(condition.group(1))
                tally.branches += int(condition.group(2))
    return tally.reading()


def _read_lcov(text: str, include: FileFilter) -> CoverageReading:
    tally = _Tally()
    records = 0
    counting = False
    for raw in text.splitlines():
        line = raw.strip()
        key, _, value = line.partition(":")
        if key == "SF":
            records += 1
            counting = include(value.strip())
        elif not counting:
            continue
        elif key == "DA":
            fields = value.split(",")
            tally.lines += 1
            tally.lines_hit += len(fields) > 1 and _int(fields[1]) > 0
        elif key == "BRDA":
            fields = value.split(",")
            tally.branches += 1
            tally.branches_hit += len(fields) > 3 and fields[3] not in ("-", "") and _int(fields[3]) > 0
    if records == 0:
        return UNAVAILABLE
    return tally.reading()


def production_filter(is_test_or_excluded: Callable[[str], bool],
                      root: str | os.PathLike[str] | None) -> FileFilter:
    """A `FileFilter` keeping the files inside the project the declared patterns do not call tests or excluded."""
    def include(name: str) -> bool:
        relative = project_relative_path(name.replace("\\", "/"), root)
        return relative is None or not is_test_or_excluded(relative)

    return include


def read_coverage(report_text: str | None, report_format: str | None, include: FileFilter = lambda _: True,
                  ) -> CoverageReading:
    """The line + branch reading of a `cobertura` or `lcov` report (`None` when none was written)."""
    if report_text is None or not report_text.strip():
        return UNAVAILABLE
    if report_format == "cobertura":
        return _read_cobertura(report_text, include)
    if report_format == "lcov":
        return _read_lcov(report_text, include)
    return UNAVAILABLE


__all__ = [
    "COVERAGE_RULE_OF_THUMB",
    "NO_CODE",
    "UNAVAILABLE",
    "CoverageReading",
    "CoverageStatus",
    "FileFilter",
    "production_filter",
    "read_coverage",
]
