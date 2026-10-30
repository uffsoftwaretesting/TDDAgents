"""
The development conversation's prompts.

The prose lives in `app/prompts/session/development/` as Markdown with `{{PLACEHOLDER}}`
substitution (§4 of `docs/transition_elaboration_plan.md`); this module is the one place each
value reaches its placeholder. The planning itself is plan mode's (the Analyst): its
instructions are upstream's `plan_mode` attachment (`app/utils/attachments.py`), and the approved
plan reaches development through `ExitPlanMode`'s result, which ends with `development_approved_prompt`.
"""

from __future__ import annotations

from pathlib import Path

from app.tdd.coverage_gate import TDD_PROMPT_VARS
from app.tdd.plan import TddPlan, render_plan
from app.utils.frontmatter_parser import render_prompt

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "session"


def _read(role: str, name: str) -> str:
    return (PROMPTS_DIR / role / name).read_text(encoding="utf-8")


def _current(plan: TddPlan) -> str:
    current = plan.current
    return f"{current.id}: {current.text}" if current is not None else "(none: every sub-requirement is done)"


def development_approved_prompt(plan: TddPlan) -> str:
    """What follows the approved plan (`ExitPlanMode`): how to work, the TDD plan, where to start."""
    values = {"PLAN": render_plan(plan), "CURRENT": _current(plan), **TDD_PROMPT_VARS}
    return render_prompt(_read("development", "approved.md"), values).strip()


def development_resume_prompt(plan: TddPlan) -> str:
    """The prompt a resumed development conversation continues with."""
    values = {"PLAN": render_plan(plan), "CURRENT": _current(plan)}
    return render_prompt(_read("development", "resume.md"), values).strip()


def development_continue_prompt(plan: TddPlan) -> str:
    """`/resume`: go on with the plan."""
    values = {"PLAN": render_plan(plan), "CURRENT": _current(plan)}
    return render_prompt(_read("development", "continue.md"), values).strip()


__all__ = [
    "PROMPTS_DIR",
    "development_approved_prompt",
    "development_continue_prompt",
    "development_resume_prompt",
]
