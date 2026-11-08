"""
The JUnit report RunTests reads (`app/tdd/junit_report.py`, 2026-11-03), in the shapes the main
ecosystems' runners write, and the declared test files a report does not name (`collection_gate.py`).
"""

from __future__ import annotations

import pytest

from app.tdd.collection_gate import project_test_files, uncollected_test_files
from app.tdd.junit_report import FailedCase, JUnitReport, parse_junit_report
from app.tdd.project_config import ProjectConfig
from tests.conftest import FakeWorkspace

#: pytest `-o junit_family=xunit1 --junitxml=…`: a `file` on every case.
PYTEST_XUNIT1 = """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" errors="0" failures="1" skipped="1" tests="4">
<testcase classname="tests.test_calc" name="test_add" file="tests/test_calc.py" line="2" time="0.001"/>
<testcase classname="tests.test_calc" name="test_sub" file="tests/test_calc.py" line="5" time="0.001">
<failure message="assert 1 == 2">E   assert 1 == 2</failure></testcase>
<testcase classname="tests.test_calc" name="test_skip" file="tests/test_calc.py" line="8" time="0">
<skipped type="pytest.skip" message="later">later</skipped></testcase>
<testcase classname="tests.test_other" name="test_x" file="tests/test_other.py" line="1" time="0"/>
</testsuite></testsuites>"""

#: jest-junit with `addFileAttribute`: absolute paths on the cases.
JEST_JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="jest tests" tests="2" failures="0" errors="0" time="0.5">
  <testsuite name="sum" errors="0" failures="0" skipped="0" timestamp="2026-11-03T10:00:00" time="0.4" tests="2">
    <testcase classname="sum adds" name="sum adds" time="0.002" file="/proj/src/sum.test.js"/>
    <testcase classname="sum subtracts" name="sum subtracts" time="0.001" file="/proj/src/sum.test.js"/>
  </testsuite>
</testsuites>"""

#: vitest `--reporter=junit`: the suite is named after its file; with `addFileAttribute` the cases carry it.
VITEST = """<?xml version="1.0" encoding="UTF-8" ?>
<testsuites name="vitest tests" tests="2" failures="0" errors="1" time="0.01">
    <testsuite name="src/math.test.ts" timestamp="2026-11-03T10:00:00" hostname="h" tests="2" failures="0"
               errors="1" skipped="0" time="0.005">
        <testcase classname="src/math.test.ts" name="adds" time="0.001" file="src/math.test.ts"/>
        <testcase classname="src/math.test.ts" name="throws" time="0.001" file="src/math.test.ts">
            <error message="boom" type="Error">Error: boom</error>
        </testcase>
    </testsuite>
</testsuites>"""

#: Maven surefire: one bare `<testsuite>` per class, no file names, a properties block.
SUREFIRE = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" name="com.example.CalcTest" time="0.02"
           tests="3" errors="0" skipped="1" failures="0">
  <properties><property name="java.version" value="21"/></properties>
  <testcase name="adds" classname="com.example.CalcTest" time="0.001"/>
  <testcase name="subtracts" classname="com.example.CalcTest" time="0.001"/>
  <testcase name="later" classname="com.example.CalcTest" time="0"><skipped/></testcase>
</testsuite>"""

#: go-junit-report: suites per package, no file names.
GO_JUNIT_REPORT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites tests="2" failures="1">
  <testsuite name="example.com/calc" tests="2" failures="1" errors="0" id="0" hostname="h" time="0.003"
             timestamp="2026-11-03T10:00:00Z">
    <properties><property name="go.version" value="go1.23"/></properties>
    <testcase name="TestAdd" classname="example.com/calc" time="0.000"/>
    <testcase name="TestSub" classname="example.com/calc" time="0.000">
      <failure message="Failed" type="">calc_test.go:12: got 1</failure>
    </testcase>
  </testsuite>
</testsuites>"""

#: cargo-nextest: a namespaced document is read the same way.
NEXTEST = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites xmlns="urn:example" name="nextest-run" tests="2" failures="0" errors="0" uuid="u" timestamp="t"
            time="0.1">
  <testsuite name="calc" tests="2" disabled="0" errors="0" failures="0">
    <testcase name="tests::adds" classname="calc" timestamp="t" time="0.01"/>
    <testcase name="tests::subs" classname="calc" timestamp="t" time="0.01"/>
  </testsuite>
</testsuites>"""


@pytest.mark.parametrize(("text", "expected"), [
    (PYTEST_XUNIT1, JUnitReport(
        tests=4, failures=1, skipped=1, files=frozenset({"tests/test_calc.py", "tests/test_other.py"}),
        failed_cases=(FailedCase("failure", "tests.test_calc", "test_sub", "tests/test_calc.py", "assert 1 == 2"),))),
    (JEST_JUNIT, JUnitReport(tests=2, files=frozenset({"src/sum.test.js"}))),
    (VITEST, JUnitReport(tests=2, errors=1, files=frozenset({"src/math.test.ts"}), failed_cases=(
        FailedCase("error", "src/math.test.ts", "throws", "src/math.test.ts", "boom"),))),
    (SUREFIRE, JUnitReport(tests=3, skipped=1)),
    (GO_JUNIT_REPORT, JUnitReport(tests=2, failures=1, failed_cases=(
        FailedCase("failure", "example.com/calc", "TestSub", None, "Failed"),))),
    (NEXTEST, JUnitReport(tests=2)),
], ids=["pytest-xunit1", "jest-junit", "vitest", "surefire", "go-junit-report", "nextest"])
def test_each_ecosystems_report_is_read_by_its_test_cases(text: str, expected: JUnitReport):
    report = parse_junit_report(text, "/proj")
    assert report == expected
    assert report is not None
    assert report.passed == expected.tests - expected.failures - expected.errors - expected.skipped


def test_a_suite_file_attribute_names_its_cases_and_nested_suites_are_walked():
    text = ('<testsuites><testsuite name="outer" file="spec/a_spec.rb"><testsuite name="inner">'
            '<testcase name="x"/></testsuite><testcase name="y" file="./spec/b_spec.rb"/></testsuite></testsuites>')
    assert parse_junit_report(text) == JUnitReport(tests=2, files=frozenset({"spec/a_spec.rb", "spec/b_spec.rb"}))


def test_files_are_unknown_unless_every_case_names_one():
    text = '<testsuite><testcase name="a" file="t/a.py"/><testcase name="b"/></testsuite>'
    assert parse_junit_report(text) == JUnitReport(tests=2, files=None)
    assert parse_junit_report('<testsuite><testcase name="a" file="  "/></testsuite>') == JUnitReport(tests=1)


def test_file_names_are_normalized_against_the_project_root():
    """rspec writes `./spec/…`, PHPUnit absolute paths, Windows runners backslashes."""
    text = ('<testsuite><testcase name="a" file="./spec/a_spec.rb"/><testcase name="b" file="/proj/tests/BTest.php"/>'
            '<testcase name="c" file="tests\\\\win\\\\c_test.py"/><testcase name="d" file="/elsewhere/d.py"/>'
            "</testsuite>")
    report = parse_junit_report(text, "/proj")
    assert report is not None and report.files == frozenset(
        {"spec/a_spec.rb", "tests/BTest.php", "tests/win/c_test.py", "/elsewhere/d.py"})


def test_a_case_with_several_outcomes_counts_once_failure_first():
    text = ('<testsuite><testcase name="a"><error/><failure/></testcase>'
            '<testcase name="b"><skipped/><error/></testcase></testsuite>')
    assert parse_junit_report(text) == JUnitReport(tests=2, failures=1, errors=1, failed_cases=(
        FailedCase("failure", "", "a"), FailedCase("error", "", "b")))


def test_a_bare_testcase_is_a_report():
    assert parse_junit_report('<testcase name="a" file="t.py"/>') == JUnitReport(tests=1, files=frozenset({"t.py"}))


@pytest.mark.parametrize("text", [None, "", "   ", "not xml <", "<html><body/></html>"])
def test_no_readable_report_is_none(text: str | None):
    assert parse_junit_report(text) is None


def test_an_empty_suite_records_no_tests():
    assert parse_junit_report("<testsuites/>") == JUnitReport(tests=0)


# ── the declared test files a report does not name ─────────────────────────


CONFIG = ProjectConfig(test="t", test_files=("*_test.go", "tests/**/test_*.py"), test_support_files=("tests/data/",),
                       exclude=("vendor/",))


def test_the_projects_test_files_are_the_declared_ones_outside_the_excluded_paths():
    ws = FakeWorkspace(files={"calc_test.go": "", "pkg/a_test.go": "", "vendor/x/y_test.go": "", "calc.go": "",
                              "tests/test_a.py": "", "tests/data/test_fixture.py": "", "tests/helper.py": ""})
    # `tests/data/test_fixture.py` matches `testFiles` but is declared support: never expected in a report.
    assert project_test_files(ws, CONFIG.rules) == frozenset({"calc_test.go", "pkg/a_test.go", "tests/test_a.py"})
    assert project_test_files(object(), CONFIG.rules) is None


def test_uncollected_files_are_the_declared_test_files_the_report_lacks():
    ws = FakeWorkspace(files={"a_test.go": "", "b_test.go": ""})
    assert uncollected_test_files(ws, CONFIG.rules, frozenset({"a_test.go"})) == ("b_test.go",)
    assert uncollected_test_files(ws, CONFIG.rules, frozenset({"a_test.go", "b_test.go", "other"})) == ()
    # Unknown on either side: nothing to say.
    assert uncollected_test_files(ws, CONFIG.rules, None) is None
    assert uncollected_test_files(ws, None, frozenset()) is None
    assert uncollected_test_files(object(), CONFIG.rules, frozenset()) is None


def test_test_support_is_never_expected_in_a_report_even_when_test_files_match_it():
    """The md→pdf rerun (2026-11-08): `vitest.config.ts` in both lists kept GREEN from counting."""
    config = ProjectConfig(test="npx vitest run", test_files=("tests/", "vitest.config.ts"),
                           test_support_files=("tests/helpers/", "vitest.config.ts"))
    ws = FakeWorkspace(files={"tests/a.test.ts": "", "tests/helpers/fake.ts": "", "vitest.config.ts": "",
                              "src/a.ts": ""})
    rules = config.rules
    assert project_test_files(ws, rules) == frozenset({"tests/a.test.ts"})
    assert uncollected_test_files(ws, rules, frozenset({"tests/a.test.ts"})) == ()
    # A test file only `testFiles` matches is still expected.
    assert uncollected_test_files(ws, rules, frozenset()) == ("tests/a.test.ts",)
    # The phase tier and the fingerprint still treat both as tests.
    assert rules.is_test_code("vitest.config.ts") and rules.is_test_code("tests/helpers/fake.ts")
    assert not rules.is_test_file("vitest.config.ts") and rules.is_support_file("vitest.config.ts")
    assert not rules.is_test_code("src/a.ts")


def test_the_failed_cases_are_listed_in_document_order_with_their_first_message_line():
    text = """<testsuites>
<testsuite name="s" file="/proj/tests/a.test.ts">
  <testcase classname="tests/a.test.ts" name="first">
    <failure message="expected 1&#10;to be 2">trace</failure></testcase>
  <testcase classname="tests/a.test.ts" name="ok"/>
  <testcase classname="tests/a.test.ts" name="later"><skipped/></testcase>
</testsuite>
<testsuite name="t">
  <testcase classname="C" name="C"><error>

  Boom: no message attribute
  at line 3</error></testcase>
  <testcase classname="" name="last"><failure/></testcase>
</testsuite></testsuites>"""
    report = parse_junit_report(text, "/proj")
    assert report is not None and report.failures == 2 and report.errors == 1
    assert report.failed_cases == (
        FailedCase("failure", "tests/a.test.ts", "first", "tests/a.test.ts", "expected 1"),
        FailedCase("error", "C", "C", None, "Boom: no message attribute"),
        FailedCase("failure", "", "last", None, None),
    )
    assert [case.label for case in report.failed_cases] == ["tests/a.test.ts > first", "C", "last"]
