"""
Phase 12: the coverage reading REFACTOR is judged by (`app/tdd/coverage_gate.py`).
"""

from __future__ import annotations

import json

import pytest

from app.tdd.coverage_gate import (
    COVERAGE_CONFIG,
    COVERAGE_CONFIG_PATH,
    COVERAGE_DIR,
    COVERAGE_REPORT_PATH,
    COVERAGE_THRESHOLD,
    CoverageReading,
    CoverageStatus,
    read_coverage,
)


def report(percent: float, statements: int = 10) -> str:
    return json.dumps({"totals": {"percent_covered": percent, "num_statements": statements}})


def test_the_threshold_is_the_users_ninety_percent():
    assert COVERAGE_THRESHOLD == 90.0


def test_the_files_live_in_the_workspaces_protected_tddagents_directory():
    assert COVERAGE_DIR == ".tddagents/coverage"
    assert COVERAGE_CONFIG_PATH == ".tddagents/coverage/coveragerc"
    assert COVERAGE_REPORT_PATH == ".tddagents/coverage/report.json"


def test_the_config_measures_branches_and_leaves_the_tests_out():
    assert COVERAGE_CONFIG == (
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
        "    .venv/*\n"
    )


@pytest.mark.parametrize("percent, met", [(90.0, True), (100.0, True), (89.99, False), (0.0, False)])
def test_a_measured_figure_meets_the_threshold_at_ninety(percent, met):
    reading = read_coverage(report(percent), output="")
    assert reading == CoverageReading(CoverageStatus.MEASURED, percent)
    assert reading.met is met


def test_no_production_code_counts_as_met():
    reading = read_coverage(None, output="...\nWARNING: Failed to generate report: No data to report.\n")
    assert reading == CoverageReading(CoverageStatus.NO_CODE, None)
    assert reading.met is True


def test_a_missing_report_without_the_no_data_notice_is_unavailable():
    reading = read_coverage(None, output="some other output")
    assert reading == CoverageReading(CoverageStatus.UNAVAILABLE, None)
    assert reading.met is False


@pytest.mark.parametrize("text", ["not json", "[]", '{"totals": {}}', '{"totals": {"percent_covered": "x"}}',
                                  '{"files": {}}'])
def test_an_unreadable_report_is_unavailable(text):
    assert read_coverage(text, output="No data to report.") == CoverageReading(CoverageStatus.UNAVAILABLE, None)


def test_a_report_with_no_statements_is_no_code():
    assert read_coverage(report(100.0, statements=0), output="") == CoverageReading(CoverageStatus.NO_CODE, None)


@pytest.mark.parametrize("reading, text", [
    (CoverageReading(CoverageStatus.MEASURED, 84.25), "coverage 84.2% (needs 90%)"),
    (CoverageReading(CoverageStatus.MEASURED, 90.0), "coverage 90.0% (needs 90%)"),
    (CoverageReading(CoverageStatus.NO_CODE, None), "coverage: no production code to measure (counts as met)"),
    (CoverageReading(CoverageStatus.UNAVAILABLE, None), "coverage could not be measured"),
])
def test_the_reading_describes_itself(reading, text):
    assert reading.describe() == text
