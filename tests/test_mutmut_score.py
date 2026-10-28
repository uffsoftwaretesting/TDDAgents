"""The gate's score, computed from `mutmut results --all true` by status rather than by line
count (Phase 7: 107 timeouts were once tallied as survivors). Since Phase 11 a timeout is never
dropped: it is re-run alone first, and one that still hangs counts as detected."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mutmut_score.py"
_spec = importlib.util.spec_from_file_location("mutmut_score", _PATH)
assert _spec is not None and _spec.loader is not None
mutmut_score = importlib.util.module_from_spec(_spec)
sys.modules["mutmut_score"] = mutmut_score
_spec.loader.exec_module(mutmut_score)

RESULTS = """
    app.loop.a.x_f__mutmut_1: killed
    app.loop.a.x_f__mutmut_2: survived
    app.loop.a.x_f__mutmut_3: timeout
    app.loop.a.xǁCǁm__mutmut_4: survived
    app.loop.a.x_g__mutmut_5: killed
    app.loop.a.x_g__mutmut_6: no tests
"""


def test_statuses_are_counted_by_label():
    summary = mutmut_score.summarize(RESULTS.splitlines())
    assert summary.counts == {"killed": 2, "survived": 2, "timeout": 1, "no tests": 1}
    assert summary.total == 6


def test_timeouts_have_no_score_until_they_are_reverified():
    summary = mutmut_score.summarize(RESULTS.splitlines())
    with pytest.raises(ValueError, match=(
            r"^1 timeouts are not re-verified: re-run them alone \(scripts/mutmut_score.py --timeouts "
            r"lists them\), then score with --timeouts-reverified$")):
        summary.score(timeouts_reverified=False)


def test_a_reverified_timeout_counts_as_detected_and_stays_in_the_denominator():
    summary = mutmut_score.summarize(RESULTS.splitlines())
    assert summary.score(timeouts_reverified=True) == pytest.approx(3 / 6)


def test_a_run_without_timeouts_needs_no_reverification():
    summary = mutmut_score.summarize(["    a.x_f__mutmut_1: killed", "    a.x_f__mutmut_2: survived"])
    assert summary.score(timeouts_reverified=False) == pytest.approx(1 / 2)


def test_the_timed_out_mutants_are_listed_by_name():
    assert mutmut_score.summarize(RESULTS.splitlines()).timeouts == ("app.loop.a.x_f__mutmut_3",)


def test_survivors_are_grouped_by_function_and_timeouts_are_not_survivors():
    summary = mutmut_score.summarize(RESULTS.splitlines())
    assert summary.survivors_by_function == {"app.loop.a.x_f": 1, "app.loop.a.xǁCǁm": 1, "app.loop.a.x_g": 1}


def test_an_unfinished_run_has_no_score():
    with pytest.raises(ValueError, match="^2 mutants were not checked; the run is incomplete$"):
        mutmut_score.summarize(["    a.x_f__mutmut_1: not checked", "    a.x_f__mutmut_2: not checked"]).score(
            timeouts_reverified=True)


def test_an_empty_run_has_no_score():
    with pytest.raises(ValueError, match="^no mutants were measured"):
        mutmut_score.summarize([]).score(timeouts_reverified=True)


def test_the_report_names_the_gate_figures():
    report = mutmut_score.render(mutmut_score.summarize(RESULTS.splitlines()), timeouts_reverified=True)
    assert report.splitlines()[:3] == [
        "mutants: 6 (timeout 1, re-verified: still hanging, counted as detected)",
        "killed: 2, survived: 2, no tests: 1",
        "score: 50.0% ((2 killed + 1 timeout) / 6)",
    ]
    assert "  1  app.loop.a.x_f" in report


def test_the_report_without_reverification_says_why_there_is_no_score():
    report = mutmut_score.render(mutmut_score.summarize(RESULTS.splitlines()), timeouts_reverified=False)
    assert report.splitlines()[:3] == [
        "mutants: 6 (timeout 1, NOT re-verified)",
        "killed: 2, survived: 2, no tests: 1",
        ("score: none — 1 timeouts are not re-verified: re-run them alone (scripts/mutmut_score.py "
         "--timeouts lists them), then score with --timeouts-reverified"),
    ]
