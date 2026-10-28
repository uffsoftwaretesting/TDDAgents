"""
The coverage REFACTOR is judged by (Phase 12).

REFACTOR is mandatory: a sub-requirement is done only when a RunTests run in REFACTOR passes
and line + branch coverage of the production code reaches `COVERAGE_THRESHOLD` (user decision,
2026-10-28). `RunTests` measures it with pytest-cov on every run: the config below omits the
test files, the JSON report is read back through the workspace, and both live in the
workspace's `.tddagents/` directory, which the agents' file tools protect and the export skips.

A run with no production code to measure counts as met (user decision): coverage.py then
writes no report and says "No data to report", or reports zero statements. Any other missing
or unreadable report is `UNAVAILABLE`, which never meets the threshold.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: Line + branch coverage of the production code a REFACTOR run must reach (user decision).
COVERAGE_THRESHOLD = 90.0

COVERAGE_DIR = ".tddagents/coverage"
COVERAGE_CONFIG_PATH = f"{COVERAGE_DIR}/coveragerc"
COVERAGE_REPORT_PATH = f"{COVERAGE_DIR}/report.json"

#: Test paths as `app/tdd/phase_permissions.is_test_path` classifies them, and our own files.
COVERAGE_CONFIG = (
    "[run]\n"
    "branch = True\n"
    "omit =\n"
    "    tests/*\n"
    "    */tests/*\n"
    "    test/*\n"
    "    */test/*\n"
    "    test_*.py\n"
    "    */test_*.py\n"
    "    *_test.py\n"
    "    conftest.py\n"
    "    */conftest.py\n"
    "    .tddagents/*\n"
)

#: What pytest-cov prints when nothing was measured.
NO_DATA_NOTICE = "No data to report"


class CoverageStatus(StrEnum):
    MEASURED = "measured"
    NO_CODE = "no_code"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CoverageReading:
    status: CoverageStatus
    percent: float | None = None

    @property
    def met(self) -> bool:
        if self.status == CoverageStatus.NO_CODE:
            return True
        return self.status == CoverageStatus.MEASURED and self.percent is not None \
            and self.percent >= COVERAGE_THRESHOLD

    def describe(self) -> str:
        if self.status == CoverageStatus.MEASURED:
            return f"coverage {self.percent:.1f}% (needs {COVERAGE_THRESHOLD:.0f}%)"
        if self.status == CoverageStatus.NO_CODE:
            return "coverage: no production code to measure (counts as met)"
        return "coverage could not be measured"


UNAVAILABLE = CoverageReading(CoverageStatus.UNAVAILABLE)


def read_coverage(report_text: str | None, output: str) -> CoverageReading:
    """The reading from pytest-cov's JSON report (`None` when none was written) and the run's output."""
    if report_text is None:
        if NO_DATA_NOTICE in output:
            return CoverageReading(CoverageStatus.NO_CODE)
        return UNAVAILABLE
    try:
        data: Any = json.loads(report_text)
        totals = data["totals"]
        percent = totals["percent_covered"]
        statements = totals["num_statements"]
    except (ValueError, TypeError, KeyError):
        return UNAVAILABLE
    if not isinstance(percent, (int, float)) or not isinstance(statements, int):
        return UNAVAILABLE
    if statements == 0:
        return CoverageReading(CoverageStatus.NO_CODE)
    return CoverageReading(CoverageStatus.MEASURED, float(percent))
