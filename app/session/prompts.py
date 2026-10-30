"""
The session shell's prompts: the analyst, the engineer and the planner (Phase 9).

The prose lives in `app/prompts/session/<role>/` as Markdown with `{{PLACEHOLDER}}`
substitution (§4 of `docs/transition_elaboration_plan.md`); this module is the one place
each value reaches its placeholder. What the Jinja2 templates expressed with `{% if %}` is
decided here in code: an absent conversation history leaves its section out, rather than
rendering an empty heading.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.tdd.coverage_gate import TDD_PROMPT_VARS
from app.tdd.plan import TddPlan, render_plan
from app.utils.frontmatter_parser import render_prompt

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "session"


def _read(role: str, name: str) -> str:
    return (PROMPTS_DIR / role / name).read_text(encoding="utf-8")


def _system(role: str) -> str:
    return render_prompt(_read(role, "system.md")).strip()


def analyst_prompts(user_input: str, conversation_history: str = "") -> tuple[str, str]:
    """The analyst's (system, human) prompts for one turn of requirements gathering."""
    history = ""
    if conversation_history:
        history = render_prompt(_read("analyst", "history.md"), {"CONVERSATION_HISTORY": conversation_history})
        history = history.strip() + "\n\n"
    human = render_prompt(
        _read("analyst", "request.md"),
        {"USER_INPUT": user_input, "CONVERSATION_HISTORY_SECTION": history},
    )
    return _system("analyst"), human.strip()


def engineer_prompts(
    requirements: str, conversation_history: str = "", *, specification: str = "", feedback: str = ""
) -> tuple[str, str]:
    """
    The engineer's (system, human) prompts: the history when there is one, else the requirements.
    With the user's review feedback (upstream's "No, keep planning"), the request carries the
    previous specification and the feedback.
    """
    values = {"REQUIREMENTS": conversation_history or requirements}
    if feedback:
        human = render_prompt(_read("engineer", "feedback.md"),
                              {**values, "SPECIFICATION": specification, "FEEDBACK": feedback})
    else:
        human = render_prompt(_read("engineer", "request.md"), values)
    return _system("engineer"), human.strip()


def planner_prompts(
    specification: str, completed: Sequence[str] = (), *, plan: Sequence[str] = (), feedback: str = ""
) -> tuple[str, str]:
    """
    The planner's (system, human) prompts for the engineer's specification; on a re-scope
    (`/requirements`, Phase 11) the sub-requirements already done are listed. With the user's
    review feedback, the previous plan and the feedback follow.
    """
    human = render_prompt(_read("planner", "request.md"), {"SPECIFICATION": specification}).strip()
    if completed:
        done = render_prompt(_read("planner", "completed.md"),
                             {"COMPLETED": "\n".join(f"- {item}" for item in completed)}).strip()
        human = f"{human}\n\n{done}"
    if feedback:
        revise = render_prompt(_read("planner", "feedback.md"), {
            "PLAN": "\n".join(f"{n}. {item}" for n, item in enumerate(plan, 1)), "FEEDBACK": feedback}).strip()
        human = f"{human}\n\n{revise}"
    return _system("planner"), human


def _current(plan: TddPlan) -> str:
    current = plan.current
    return f"{current.id}: {current.text}" if current is not None else "(none: every sub-requirement is done)"


def development_start_prompt(specification: str, plan: TddPlan) -> str:
    """The first prompt of the development conversation (Phase 11): the spec, the plan, where to start."""
    return render_prompt(
        _read("development", "start.md"),
        {"SPECIFICATION": specification.strip() or "(none)", "PLAN": render_plan(plan), "CURRENT": _current(plan),
         **TDD_PROMPT_VARS},
    ).strip()


def development_resume_prompt(plan: TddPlan) -> str:
    """The prompt a resumed development conversation continues with."""
    values = {"PLAN": render_plan(plan), "CURRENT": _current(plan)}
    return render_prompt(_read("development", "resume.md"), values).strip()


def development_continue_prompt(plan: TddPlan) -> str:
    """`/resume`: go on with the plan."""
    values = {"PLAN": render_plan(plan), "CURRENT": _current(plan)}
    return render_prompt(_read("development", "continue.md"), values).strip()


def development_replanned_prompt(specification: str, plan: TddPlan) -> str:
    """After `/requirements`: the new specification and plan replace the old ones."""
    values = {"SPECIFICATION": specification.strip() or "(none)", "PLAN": render_plan(plan), "CURRENT": _current(plan)}
    return render_prompt(_read("development", "replanned.md"), values).strip()


__all__ = [
    "PROMPTS_DIR",
    "analyst_prompts",
    "development_continue_prompt",
    "development_replanned_prompt",
    "development_resume_prompt",
    "development_start_prompt",
    "engineer_prompts",
    "planner_prompts",
]
