"""
The session shell's prompts: the analyst, the engineer and the planner (Phase 9).

The prose lives in `app/prompts/session/<role>/` as Markdown with `{{PLACEHOLDER}}`
substitution (§4 of `docs/transition_elaboration_plan.md`); this module is the one place
each value reaches its placeholder. What the Jinja2 templates expressed with `{% if %}` is
decided here in code: an absent conversation history leaves its section out, rather than
rendering an empty heading.
"""

from __future__ import annotations

from pathlib import Path

from app.loop.prompts.loader import render_prompt

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


def engineer_prompts(requirements: str, conversation_history: str = "") -> tuple[str, str]:
    """The engineer's (system, human) prompts: the history when there is one, else the requirements."""
    human = render_prompt(_read("engineer", "request.md"), {"REQUIREMENTS": conversation_history or requirements})
    return _system("engineer"), human.strip()


def planner_prompts(specification: str) -> tuple[str, str]:
    """The planner's (system, human) prompts for the engineer's specification."""
    human = render_prompt(_read("planner", "request.md"), {"SPECIFICATION": specification})
    return _system("planner"), human.strip()


__all__ = ["PROMPTS_DIR", "analyst_prompts", "engineer_prompts", "planner_prompts"]
