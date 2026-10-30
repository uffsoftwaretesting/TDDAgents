"""
The development agents' "Doing tasks" section is upstream's `getSimpleDoingTasksSection`
verbatim (2026-10-28). Planning is plan mode's, and the TDD contract states how a plan runs.

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
#: The internal-build (`USER_TYPE === 'ant'`) Doing-tasks items this build ports (2026-10-28).
PORTED_ANT_ITEMS = ("Before reporting a task complete, verify it actually works", "Report outcomes faithfully")


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
    # Upstream's own words against faking a green result (2026-10-28).
    assert "never suppress or simplify failing checks (tests, lints, type errors) to manufacture a green result" in text
    assert "Before reporting a task complete, verify it actually works" in text


def _external_items(source: str) -> list[str]:
    """`getSimpleDoingTasksSection`'s bullets as the external build renders them."""
    body = source.split("function getSimpleDoingTasksSection(): string {", 1)[1].split("return [`# Doing tasks`", 1)[0]
    # The `USER_TYPE === 'ant'` spreads are not in the external build, except the two items ported
    # against faked green results (2026-10-28, user decision): "verify it actually works" and
    # "Report outcomes faithfully".

    def keep_ported(spread: re.Match[str]) -> str:
        kept = [f"`{item}`," for item in re.findall(r"`([^`]*)`", spread.group(0)) if item.startswith(PORTED_ANT_ITEMS)]
        return "\n".join(kept)

    body = re.sub(r"\.\.\.\(process\.env\.USER_TYPE === 'ant'\s*\?\s*\[.*?\]\s*:\s*\[\]\),", keep_ported, body,
                  flags=re.DOTALL)
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


def test_the_session_prompts_are_gone():
    """Planning is plan mode's (2026-10-30): the engineer and planner prompts were removed with them."""
    assert not (SESSION / "planner").exists() and not (SESSION / "engineer").exists()


def test_the_contract_tells_the_analyst_how_the_plan_runs():
    """Two live palindrome plans named `tests/tests_*.py`, which pytest never collects (2026-10-28)."""
    text = read_system_prompt_file("tdd-contract.md")
    assert "numbered items under its `## Implementation Plan` heading become the sub-requirements" in text
    assert ("RunTests runs pytest and reports the test files pytest does not collect. pytest's default "
            "discovery collects `test_*.py` and `*_test.py` files") in text
    assert "exactly one Red -> Green -> Refactor cycle that the RunTests tool observes" in text
    for rule in ("unless", "README", " CI", "packaging", "MUST ALWAYS", "gold standard", "tests_"):
        assert rule not in text, rule
