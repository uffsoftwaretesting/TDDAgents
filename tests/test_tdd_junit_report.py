"""
The JUnit report RunTests reads (`app/tdd/junit_report.py`, 2026-11-03), in the shapes the main
ecosystems' runners write, what a report names and the declared test files a run did not run
(`collection_gate.py`, strict since 2026-11-08).
"""

from __future__ import annotations

import pytest

from app.tdd.collection_gate import Collection, observe_collection, project_test_files, resolve_counts, resolve_ran
from app.tdd.junit_report import FailedCase, JUnitReport, parse_junit_report
from app.tdd.project_config import ProjectConfig
from app.tdd.test_listing import TestListing
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


def _report(**kwargs: object) -> JUnitReport:
    return JUnitReport(tests=1, **kwargs)  # type: ignore[arg-type]


def test_the_collection_is_the_declared_test_files_the_run_did_not_run():
    ws = FakeWorkspace(files={"a_test.go": "", "b_test.go": ""})
    assert observe_collection(ws, CONFIG.rules, _report(files=frozenset({"a_test.go"}))) == Collection(
        frozenset({"a_test.go"}), ("b_test.go",))
    both = frozenset({"a_test.go", "b_test.go"})
    assert observe_collection(ws, CONFIG.rules, _report(files=both)) == Collection(both, ())
    # Nothing to observe it from: no declaration, no report, a report without tests.
    assert observe_collection(ws, None, _report(files=both)) is None
    assert observe_collection(ws, CONFIG.rules, None) is None
    assert observe_collection(ws, CONFIG.rules, JUnitReport(tests=0)) is None


def test_files_the_report_names_outside_the_declaration_are_undeclared():
    """Gap (b): inside the project and not test code; outside the project it is not the declaration's."""
    ws = FakeWorkspace(files={"a_test.go": "", "tests/data/x.py": "", "calc.go": ""})
    ran = frozenset({"a_test.go", "calc.go", "tests/data/x.py", "/elsewhere/z_test.go", "../up_test.go"})
    collection = observe_collection(ws, CONFIG.rules, _report(files=ran))
    assert collection == Collection(ran, (), ("calc.go",))


def test_unknown_files_and_an_unlistable_project_are_stated_apart():
    ws = FakeWorkspace(files={"a_test.go": ""})
    unknown = observe_collection(ws, CONFIG.rules, _report())
    assert unknown == Collection(None, None)
    assert unknown is not None and unknown.unknown == ("the JUnit report names no test files",)
    unlistable = observe_collection(object(), CONFIG.rules, _report(files=frozenset({"a_test.go"})))
    assert unlistable == Collection(frozenset({"a_test.go"}), None)
    assert unlistable is not None and unlistable.unknown == ("the project's files could not be listed",)
    assert Collection(frozenset(), ()).unknown == ()


def test_a_listing_names_the_files_when_the_report_names_none():
    ws = FakeWorkspace(files={"a_test.go": "", "b_test.go": ""})
    listed = TestListing(command="go list", exit_code=0, files=frozenset({"a_test.go"}))
    assert observe_collection(ws, CONFIG.rules, _report(), listed) == Collection(
        frozenset({"a_test.go"}), ("b_test.go",), (), listed)
    failed = TestListing(command="go list", exit_code=2)
    collection = observe_collection(ws, CONFIG.rules, _report(), failed)
    assert collection == Collection(None, None, (), failed)
    assert collection is not None and collection.unknown == (
        "the JUnit report names no test files", "the listTests command exited 2")
    # A report that names its files wins over a listing.
    named = observe_collection(ws, CONFIG.rules, _report(files=frozenset({"a_test.go", "b_test.go"})), listed)
    assert named is not None and named.uncollected == ()


# ── what a report names (`resolve_ran`) ──────────────────────────────────────

LISTED = frozenset({"src/math.test.ts", "src/other.test.ts", "vendor/v.test.ts", "README.md"})


@pytest.mark.parametrize(("text", "ran"), [
    # The case's own `file` attribute.
    ('<testsuite><testcase classname="x" name="a" file="src/math.test.ts"/></testsuite>',
     {"src/math.test.ts"}),
    # The enclosing suite's `file` attribute.
    ('<testsuites><testsuite name="s" file="src/math.test.ts"><testcase name="a"/></testsuite></testsuites>',
     {"src/math.test.ts"}),
    # vitest: the classname is the project-relative path.
    ('<testsuite name="x"><testcase classname="src/math.test.ts" name="adds"/></testsuite>',
     {"src/math.test.ts"}),
    # The enclosing suite's name is the path (nested: an outer suite names it).
    ('<testsuites><testsuite name="src/other.test.ts"><testsuite name="adds"><testcase classname="Calc" name="a"/>'
     '</testsuite></testsuite></testsuites>', {"src/other.test.ts"}),
    # Mixed: one case by attribute, one by an absolute classname inside the project.
    ('<testsuite><testcase name="a" file="src/math.test.ts"/><testcase classname="/proj/src/other.test.ts" '
     'name="b"/></testsuite>', {"src/math.test.ts", "src/other.test.ts"}),
    # One unresolved case: the report names no files.
    ('<testsuite name="calc"><testcase name="a" file="src/math.test.ts"/><testcase classname="com.x.CalcTest" '
     'name="b"/></testsuite>', None),
    # A classname that is a file only under an excluded path names nothing.
    ('<testsuite><testcase classname="vendor/v.test.ts" name="a"/></testsuite>', None),
    # surefire, go-junit-report, nextest name no file of the project.
    (SUREFIRE, None), (GO_JUNIT_REPORT, None), (NEXTEST, None),
], ids=["case-file", "suite-file", "classname", "suite-name", "mixed", "one-unresolved", "excluded", "surefire",
        "go", "nextest"])
def test_what_a_report_names(text: str, ran: set[str] | None):
    rules = ProjectConfig(test="t", test_files=("src/",), exclude=("vendor/",)).rules
    report = parse_junit_report(text, "/proj")
    assert report is not None
    assert resolve_ran(report, LISTED, rules) == (frozenset(ran) if ran is not None else None)


def test_without_a_listing_only_file_attributes_name_files():
    rules = ProjectConfig(test="t", test_files=("src/",)).rules
    vitest = parse_junit_report('<testsuite><testcase classname="src/math.test.ts" name="a"/></testsuite>')
    assert vitest is not None and resolve_ran(vitest, None, rules) is None
    named = parse_junit_report('<testsuite><testcase name="a" file="src/math.test.ts"/></testsuite>')
    assert named is not None and resolve_ran(named, None, rules) == frozenset({"src/math.test.ts"})
    # A report built by hand, with no case left unnamed, names its `files`.
    assert resolve_ran(JUnitReport(tests=1), LISTED, rules) is None


def test_a_case_without_a_file_keeps_its_classname_then_its_suites_innermost_first():
    text = ('<testsuites name="root"><testsuite name="./outer.ts"><testsuite name="inner">'
            '<testcase classname="C" name="a"/><testcase classname="inner" name="b"/><testcase name="d"/>'
            '</testsuite><testcase name="c" file="f.ts"/></testsuite></testsuites>')
    report = parse_junit_report(text)
    assert report is not None
    assert report.unnamed == (("C", "inner", "outer.ts"), ("inner", "outer.ts"), ("inner", "outer.ts"))
    assert report.named == frozenset({"f.ts"}) and report.files is None
    assert report.named_counts == (("f.ts", 1),)


def test_the_cases_each_file_names_are_counted():
    report = parse_junit_report(PYTEST_XUNIT1)
    assert report is not None and report.named_counts == (("tests/test_calc.py", 3), ("tests/test_other.py", 1))
    rules = ProjectConfig(test="t", test_files=("src/",), exclude=("vendor/",)).rules
    text = ('<testsuite><testcase name="a" file="src/math.test.ts"/><testcase classname="src/math.test.ts" name="b"/>'
            '<testcase classname="src/other.test.ts" name="c"/></testsuite>')
    mixed = parse_junit_report(text)
    assert mixed is not None
    assert resolve_counts(mixed, LISTED, rules) == {"src/math.test.ts": 2, "src/other.test.ts": 1}
    assert resolve_counts(mixed, None, rules) is None
    unresolved = parse_junit_report('<testsuite><testcase classname="com.x.T" name="a"/></testsuite>')
    assert unresolved is not None and resolve_counts(unresolved, LISTED, rules) is None
    # A report built by hand with `files` only carries no counts.
    assert resolve_counts(JUnitReport(tests=1, files=frozenset({"a"})), LISTED, rules) is None


def test_the_collection_carries_the_counts_only_when_the_report_names_the_files():
    ws = FakeWorkspace(files={"src/math.test.ts": "", "src/other.test.ts": ""})
    rules = ProjectConfig(test="t", test_files=("src/",)).rules
    vitest = parse_junit_report('<testsuite><testcase classname="src/math.test.ts" name="a"/>'
                                '<testcase classname="src/other.test.ts" name="b"/></testsuite>')
    collection = observe_collection(ws, rules, vitest)
    assert collection is not None and collection.counts == (("src/math.test.ts", 1), ("src/other.test.ts", 1))
    surefire = parse_junit_report('<testsuite><testcase classname="com.x.T" name="a"/></testsuite>')
    listing = TestListing(command="l", exit_code=0, files=frozenset({"src/math.test.ts", "src/other.test.ts"}))
    listed = observe_collection(ws, rules, surefire, listing)
    assert listed is not None and listed.ran is not None and listed.counts is None


def test_test_support_is_never_expected_in_a_report_even_when_test_files_match_it():
    """The md→pdf rerun (2026-11-08): `vitest.config.ts` in both lists kept GREEN from counting."""
    config = ProjectConfig(test="npx vitest run", test_files=("tests/", "vitest.config.ts"),
                           test_support_files=("tests/helpers/", "vitest.config.ts"))
    ws = FakeWorkspace(files={"tests/a.test.ts": "", "tests/helpers/fake.ts": "", "vitest.config.ts": "",
                              "src/a.ts": ""})
    rules = config.rules
    assert project_test_files(ws, rules) == frozenset({"tests/a.test.ts"})
    collection = observe_collection(ws, rules, _report(files=frozenset({"tests/a.test.ts"})))
    assert collection is not None and collection.uncollected == ()
    # A test file only `testFiles` matches is still expected.
    collection = observe_collection(ws, rules, _report(files=frozenset({"vitest.config.ts"})))
    assert collection is not None and collection.uncollected == ("tests/a.test.ts",)
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


# ── a written file that is no report is told apart from no file (2026-11-10) ──

def test_the_problem_with_a_written_file_is_stated():
    from app.tdd.junit_report import junit_report_problem

    assert junit_report_problem(None) is None
    assert junit_report_problem(PYTEST_XUNIT1) is None
    assert junit_report_problem("  \n") == "the file is empty"
    # The live Java run's join: a class's closing tag deleted with the next file's declaration.
    joined = ('<testsuites>\n<testsuite name="A"><testcase name="a"/>\n'
              '<testsuite name="B"><testcase name="b"/>\n</testsuite>\n</testsuites>\n')
    assert junit_report_problem(joined) == "not valid XML: mismatched tag: line 5, column 2"
    assert junit_report_problem("<html><body/></html>") == (
        "its root element is <html>, not <testsuites>, <testsuite> or <testcase>")


def test_runtests_and_judge_word_an_unreadable_report_apart_from_a_missing_one():
    from app.tdd.ledger import TddPhase
    from app.tdd.run_judgement import NO_REPORT, UNREADABLE_REPORT, judge
    from app.tdd.run_tests_tool.run_tests_tool import report_line

    assert report_line(None) == "JUnit report: none was written"
    assert report_line(None, "the file is empty") == (
        "JUnit report: written, but not readable as a JUnit report (the file is empty)")
    missing = judge(0, None, None, TddPhase.GREEN)
    unreadable = judge(0, None, None, TddPhase.GREEN, report_problem="the file is empty")
    assert missing.not_evidence == NO_REPORT
    assert unreadable.not_evidence == f"{UNREADABLE_REPORT} (the file is empty)"
    # The decision does not change: neither is evidence.
    assert missing.is_error and unreadable.is_error and unreadable.outcome is None


def test_run_suite_reports_why_the_written_file_is_no_report(tmp_path):
    from app.tdd.run_tests_tool.run_tests_tool import report_line, run_suite
    from app.workspace.local import LocalWorkspace

    ws = LocalWorkspace(str(tmp_path))
    broken = ProjectConfig(test='printf "<testsuites><testsuite>" > "$TDDAGENTS_JUNIT_REPORT"', test_files=("t/",))
    result = run_suite(ws, broken)
    assert result.report is None and result.report_problem is not None
    assert result.report_problem.startswith("not valid XML: no element found")
    assert report_line(result.report, result.report_problem).startswith("JUnit report: written, but not readable")
    silent = ProjectConfig(test="true", test_files=("t/",))
    nothing = run_suite(ws, silent)
    assert (nothing.report, nothing.report_problem) == (None, None)
