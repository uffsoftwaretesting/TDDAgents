"""
Phase 12: the coverage reading RunTests reports (`app/tdd/coverage_gate.py`); since 2026-11-03 read
from the cobertura or lcov report the declared test command writes.
"""

from __future__ import annotations

import pytest

from app.tdd.coverage_gate import (
    COVERAGE_RULE_OF_THUMB,
    NO_CODE,
    UNAVAILABLE,
    CoverageReading,
    CoverageStatus,
    production_filter,
    read_coverage,
)
from app.tdd.project_config import ProjectConfig

#: coverage.py's cobertura: `calc.py` 3 of 4 lines and 1 of 2 branches, its test file fully covered.
COBERTURA = """<?xml version="1.0" ?>
<coverage version="7.11.3" line-rate="0.8" branch-rate="0.5" lines-covered="7" lines-valid="8"
          branches-covered="1" branches-valid="2">
  <sources><source>/proj</source></sources>
  <packages><package name="."><classes>
    <class name="calc.py" filename="calc.py" line-rate="0.75" branch-rate="0.5">
      <lines>
        <line number="1" hits="1"/>
        <line number="2" hits="1" branch="true" condition-coverage="50% (1/2)" missing-branches="4"/>
        <line number="3" hits="1"/>
        <line number="4" hits="0"/>
      </lines>
    </class>
    <class name="test_calc.py" filename="tests/test_calc.py" line-rate="1" branch-rate="1">
      <lines><line number="1" hits="1"/><line number="2" hits="1"/><line number="3" hits="1"/>
             <line number="4" hits="1"/></lines>
    </class>
  </classes></package></packages>
</coverage>"""

#: istanbul/vitest lcov: `src/sum.js` 2 of 4 lines and 1 of 2 branches, a test file and a dependency.
LCOV = """TN:
SF:/proj/src/sum.js
FN:1,sum
DA:1,1
DA:2,3
DA:3,0
DA:4,0
BRDA:2,0,0,1
BRDA:2,0,1,-
LF:4
LH:2
end_of_record
SF:src/sum.test.js
DA:1,1
end_of_record
SF:node_modules/dep/index.js
DA:1,0
end_of_record
"""

CONFIG = ProjectConfig(test="t", test_files=("*.test.js", "tests/"), exclude=("node_modules/",))


def production(root: str = "/proj"):
    rules = CONFIG.rules
    return production_filter(lambda path: rules.is_test_code(path) or rules.is_excluded(path), root)


def test_the_rule_of_thumb_is_the_users_ninety_percent():
    assert COVERAGE_RULE_OF_THUMB == 90.0


def test_cobertura_is_line_plus_branch_over_the_production_files():
    reading = read_coverage(COBERTURA, "cobertura", production())
    # (3 lines + 1 branch) / (4 lines + 2 branches)
    assert reading.status == CoverageStatus.MEASURED and reading.percent == pytest.approx(100 * 4 / 6)
    # Without a filter the test file counts too: (7 + 1) / (8 + 2).
    assert read_coverage(COBERTURA, "cobertura") == CoverageReading(CoverageStatus.MEASURED, 80.0)


def test_cobertura_without_per_file_data_reads_the_root_totals():
    text = '<coverage lines-valid="10" lines-covered="9" branches-valid="0" branches-covered="0"/>'
    assert read_coverage(text, "cobertura") == CoverageReading(CoverageStatus.MEASURED, 90.0)
    assert read_coverage('<coverage lines-valid="0" lines-covered="0" branches-valid="0" branches-covered="0"/>',
                         "cobertura") == NO_CODE
    assert read_coverage('<coverage line-rate="0.9"/>', "cobertura") == UNAVAILABLE


def test_a_line_listed_twice_counts_once():
    text = ('<coverage><packages><package><classes><class filename="a.py"><lines><line number="1" hits="1"/>'
            '</lines></class><class filename="a.py"><lines><line number="1" hits="0"/><line number="2" hits="0"/>'
            "</lines></class></classes></package></packages></coverage>")
    assert read_coverage(text, "cobertura") == CoverageReading(CoverageStatus.MEASURED, 50.0)


def test_lcov_is_line_plus_branch_over_the_production_files():
    # (2 lines + 1 branch) / (4 lines + 2 branches); the test file and the dependency are left out.
    assert read_coverage(LCOV, "lcov", production()) == CoverageReading(CoverageStatus.MEASURED, 50.0)
    assert read_coverage("TN:\nSF:a.js\nend_of_record\n", "lcov") == NO_CODE
    assert read_coverage("TN:\n", "lcov") == UNAVAILABLE


def test_only_test_files_measured_is_no_code():
    only_tests = "SF:src/sum.test.js\nDA:1,1\nend_of_record\n"
    assert read_coverage(only_tests, "lcov", production()) == NO_CODE


@pytest.mark.parametrize(("text", "fmt"), [
    (None, "lcov"), ("", "cobertura"), ("  ", "lcov"), ("not xml <", "cobertura"), ("<html/>", "cobertura"),
    (COBERTURA, "jacoco"), (COBERTURA, None),
])
def test_no_report_an_unreadable_one_or_an_unknown_format_is_unavailable(text: str | None, fmt: str | None):
    assert read_coverage(text, fmt) == UNAVAILABLE


def test_the_filter_keeps_files_outside_the_project_and_reads_backslashes():
    include = production()
    assert include("/elsewhere/lib.js") is True
    assert include("src\\a.test.js") is False
    assert include("/proj/src/a.js") is True


@pytest.mark.parametrize(("reading", "text"), [
    (CoverageReading(CoverageStatus.MEASURED, 92.5), "coverage 92.5% (rule of thumb: 90%)"),
    (NO_CODE, "coverage: no production code to measure"),
    (UNAVAILABLE, "coverage could not be measured"),
])
def test_the_reading_describes_itself(reading: CoverageReading, text: str):
    assert reading.describe() == text
