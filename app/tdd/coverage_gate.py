"""
The coverage every RunTests run measures and reports (Phase 12).

`RunTests` measures line + branch coverage of the production code with pytest-cov on every run:
the config below omits the test files, the JSON report is read back through the workspace, and
both live in the workspace's protected `.tddagents/` directory.

Coverage is guidance, not a gate (user decision, 2026-10-28): REFACTOR, still mandatory, ends on
a passing run, and raising coverage is part of its work, with `COVERAGE_RULE_OF_THUMB` as a good
rule of thumb. Until then REFACTOR had to reach 90%, which made a live run write a test that
called the code only to cover a branch the specification said must not be tested.

With no production code, coverage.py writes no report and says "No data to report", or reports
zero statements (`NO_CODE`). Any other missing or unreadable report is `UNAVAILABLE`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: Line + branch coverage of the production code that is a good rule of thumb (user decision).
COVERAGE_RULE_OF_THUMB = 90.0

#: The values prompts name as placeholders (no values in prose, §4 of the plan).
TDD_PROMPT_VARS: dict[str, str] = {"COVERAGE_RULE_OF_THUMB": f"{COVERAGE_RULE_OF_THUMB:.0f}"}

COVERAGE_DIR = ".tddagents/coverage"
COVERAGE_CONFIG_PATH = f"{COVERAGE_DIR}/coveragerc"
COVERAGE_REPORT_PATH = f"{COVERAGE_DIR}/report.json"

#: Test paths as `app/tdd/phase_permissions.is_test_path` classifies them, and our own files.
COVERAGE_CONFIG = (
    "[run]\n"
    "branch = True\n"
    "data_file = .tddagents/coverage/.coverage\n"
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
    "    .venv/*\n"
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

    def describe(self) -> str:
        if self.status == CoverageStatus.MEASURED:
            return f"coverage {self.percent:.1f}% (rule of thumb: {COVERAGE_RULE_OF_THUMB:.0f}%)"
        if self.status == CoverageStatus.NO_CODE:
            return "coverage: no production code to measure"
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
