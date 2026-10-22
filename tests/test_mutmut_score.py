"""The gate's score, computed from `mutmut results --all true` by status rather than by line
count (Phase 7: 107 timeouts were once tallied as survivors)."""

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


def test_the_score_excludes_timeouts_only():
    summary = mutmut_score.summarize(RESULTS.splitlines())
    assert summary.score == pytest.approx(2 / 5)


def test_survivors_are_grouped_by_function_and_timeouts_are_not_survivors():
    summary = mutmut_score.summarize(RESULTS.splitlines())
    assert summary.survivors_by_function == {"app.loop.a.x_f": 1, "app.loop.a.xǁCǁm": 1, "app.loop.a.x_g": 1}


def test_an_unfinished_run_has_no_score():
    with pytest.raises(ValueError, match="^2 mutants were not checked; the run is incomplete$"):
        _ = mutmut_score.summarize(["    a.x_f__mutmut_1: not checked", "    a.x_f__mutmut_2: not checked"]).score


def test_an_empty_run_has_no_score():
    with pytest.raises(ValueError, match="^no mutants were measured"):
        _ = mutmut_score.summarize([]).score


def test_the_report_names_the_gate_figures():
    report = mutmut_score.render(mutmut_score.summarize(RESULTS.splitlines()))
    assert report.splitlines()[:3] == [
        "mutants: 6 (timeout 1)",
        "killed: 2, survived: 2, no tests: 1",
        "score: 40.0% (2 / 5)",
    ]
    assert "  1  app.loop.a.x_f" in report
