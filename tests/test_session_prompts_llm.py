"""
The development conversation's prompts (`app/session/prompts.py`) and `run_sync`.

Planning is plan mode's (the Analyst); its instructions are upstream's `plan_mode` attachment,
pinned in `tests/test_plan_mode_attachments.py`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.session.prompts import (
    PROMPTS_DIR,
    development_approved_prompt,
    development_continue_prompt,
    development_resume_prompt,
)
from app.tdd.coverage_gate import TDD_PROMPT_VARS
from app.tdd.plan import ItemStatus, TddPlan, render_plan
from app.utils.run_sync import run_sync


def test_only_the_development_prompts_are_left():
    assert PROMPTS_DIR == Path(__file__).resolve().parents[1] / "app" / "prompts" / "session"
    assert sorted(p.name for p in PROMPTS_DIR.iterdir()) == ["development"]
    assert sorted(p.name for p in (PROMPTS_DIR / "development").iterdir()) == [
        "approved.md", "continue.md", "resume.md"]


def test_the_approved_prompt_names_the_cycle_the_plan_and_where_to_start():
    plan = TddPlan.from_texts(["add", "sub"])
    text = development_approved_prompt(plan)
    assert text.startswith("Implement the approved plan test-first, one sub-requirement at a time, following the "
                           "TDD plan below.")
    assert f"({TDD_PROMPT_VARS['COVERAGE_RULE_OF_THUMB']}% is a good rule of thumb)" in text
    assert f"## TDD plan\n\n{render_plan(plan)}\n\nStart with sub-requirement 1: add." in text
    assert "## Technical specification" not in text
    assert "{{" not in text


def test_the_approved_prompt_when_everything_is_done():
    plan = TddPlan.from_texts(["a"])
    plan = plan.__class__(items=(plan.items[0].__class__(id="1", text="a", status=ItemStatus.DONE),))
    assert "Start with sub-requirement (none: every sub-requirement is done)." in development_approved_prompt(plan)


def test_the_resume_and_continue_prompts():
    plan = TddPlan.from_texts(["add", "sub"])
    resume = development_resume_prompt(plan)
    assert resume.startswith("The session was resumed. Continue the TDD plan with sub-requirement 1: add.")
    assert resume.endswith(f"## TDD plan\n\n{render_plan(plan)}")
    assert development_continue_prompt(plan) == (
        f"Continue the TDD plan with sub-requirement 1: add.\n\n## TDD plan\n\n{render_plan(plan)}")


def test_run_sync_outside_and_inside_a_running_loop():
    async def answer() -> int:
        return 42

    assert run_sync(answer()) == 42

    async def nested() -> int:
        return run_sync(answer())

    assert asyncio.run(nested()) == 42
