"""
The development agents' "Doing tasks" section is upstream's `getSimpleDoingTasksSection`
verbatim (2026-10-28), and the session's planner and engineer carry the same scope rule.

A live palindrome run's plan added a README, a CI workflow and a "release preparation" step the
specification forbade. The section had been a loose paraphrase without upstream's scope rules.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.context import read_system_prompt_file

REPO = Path(__file__).resolve().parent.parent
PROMPTS_TS = REPO / "reference" / "claude-code" / "src" / "constants" / "prompts.ts"
SESSION = REPO / "app" / "prompts" / "session"


def test_the_section_carries_upstreams_scope_rules():
    text = read_system_prompt_file("doing-tasks.md")
    assert text.startswith("# Doing tasks\n - The user will primarily request you to perform software engineering")
    for rule in (
        'Don\'t add features, refactor code, or make "improvements" beyond what was asked.',
        "Do not create files unless they're absolutely necessary for achieving your goal.",
        "Don't use feature flags or backwards-compatibility shims when you can just change the code.",
        "Escalate to the user with AskUserQuestion only when you're genuinely stuck after investigation",
    ):
        assert rule in text
    assert "/help" not in text and "feedback" not in text


def _external_items(source: str) -> list[str]:
    """`getSimpleDoingTasksSection`'s bullets as the external build renders them."""
    body = source.split("function getSimpleDoingTasksSection(): string {", 1)[1].split("return [`# Doing tasks`", 1)[0]
    # The `USER_TYPE === 'ant'` spreads are not in the external build.
    body = re.sub(r"\.\.\.\(process\.env\.USER_TYPE === 'ant'\s*\?\s*\[.*?\]\s*:\s*\[\]\),", "", body, flags=re.DOTALL)
    body = re.sub(r"^\s*//.*$", "", body, flags=re.MULTILINE)

    def literals(block: str) -> list[str]:
        return [s.replace("${ASK_USER_QUESTION_TOOL_NAME}", "AskUserQuestion") for s in re.findall(r"`([^`]*)`", block)]

    code_style = literals(body.split("const codeStyleSubitems = [", 1)[1].split("]", 1)[0])
    items_block = body.split("const items = [", 1)[1]
    head, tail = items_block.split("...codeStyleSubitems,", 1)
    # The /help and feedback bullets (and their sub-items) are dropped: this build has neither.
    tail = tail.split("`If the user asks for help", 1)[0]
    return [*literals(head), *code_style, *literals(tail)]


@pytest.mark.skipif(not PROMPTS_TS.is_file(), reason="the claude-code reference snapshot is not checked out")
def test_the_section_is_upstreams_verbatim():
    expected = "\n".join(["# Doing tasks", *(f" - {item}" for item in _external_items(PROMPTS_TS.read_text()))])
    assert read_system_prompt_file("doing-tasks.md").rstrip("\n") == expected


@pytest.mark.parametrize("prompt", ["planner/system.md", "engineer/system.md"])
def test_the_session_prompts_plan_only_what_was_asked(prompt: str):
    text = (SESSION / prompt).read_text(encoding="utf-8")
    assert "no documentation, README, CI, packaging" in text
    assert "must not be implemented" in text


def test_the_planner_names_test_files_pytest_collects():
    """Two live palindrome plans named `tests/tests_*.py`, which pytest never collects (2026-10-28)."""
    text = (SESSION / "planner" / "system.md").read_text(encoding="utf-8")
    assert "use pytest's default discovery pattern (`test_*.py` or `*_test.py`, e.g. `tests/test_core.py`)" in text
    assert "tests_" not in text
