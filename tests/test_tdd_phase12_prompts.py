"""
Phase 12: every prompt the model reads states the new cycle. RED needs robust tests, not a
failure, and REFACTOR is mandatory with a coverage threshold named once, in code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.context import _file_section
from app.session.prompts import development_start_prompt
from app.tdd.coverage_gate import COVERAGE_THRESHOLD, TDD_PROMPT_VARS
from app.tdd.plan import TddPlan

REPO = Path(__file__).resolve().parents[1]
#: The old RED rule, in the forms it was written.
OLD_RULE = re.compile(
    r"write (a )?failing (unit )?tests?|failing test (first|was observed|is confirmed|confirmed)|"
    r"observe (it|a failing test) fail|must fail first|red_confirmed|tests? (files )?(are|is) strictly immutable",
    re.IGNORECASE,
)
#: TodoWrite's example wording is a sample task name, not the cycle's rule (kept as is).
EXEMPT = {"app/tools/todo_write_tool/prompt.md"}


def prompt_files() -> list[Path]:
    paths = list((REPO / "app" / "prompts").rglob("*.md")) + list((REPO / "app").rglob("prompt.md"))
    return sorted({p for p in paths if "specs" not in p.parts})


@pytest.mark.parametrize("path", prompt_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_prompt_states_the_old_red_rule(path):
    if str(path.relative_to(REPO)) in EXEMPT:
        return
    assert not OLD_RULE.search(path.read_text(encoding="utf-8"))


def test_the_threshold_is_named_once_in_code_and_filled_into_the_prompts():
    assert TDD_PROMPT_VARS == {"COVERAGE_THRESHOLD": "90"}
    contract = _file_section("tdd-contract.md")()
    start = development_start_prompt("S", TddPlan.from_texts(["a"]))
    for text in (contract, start):
        assert "{{" not in text
        assert f"{COVERAGE_THRESHOLD:.0f}%" in text
    assert "REFACTOR Phase (Mandatory)" in contract
    assert "3. REFACTOR (mandatory)" in start
