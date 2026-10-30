"""
Plan mode, ported from Claude Code (the Analyst): the plan file (`app/utils/plans.py`), the word
slug (`app/utils/words.py`), the switches (`app/utils/plan_mode_v2.py`), the mode transitions
(`app/utils/permissions/permission_setup.py`), the `Implementation Plan` parser and the gate's
plan-file carve-out.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.state.app_state_store import AppState
from app.tdd.plan import IMPLEMENTATION_PLAN_HEADING, parse_implementation_plan
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    ToolPermissionContext,
)
from app.utils import plan_mode_v2, plans, words
from app.utils.permissions.filesystem import (
    check_readable_internal_path,
    check_write_permission_for_tool,
)
from app.utils.permissions.permission_setup import (
    handle_plan_mode_transition,
    prepare_context_for_plan_mode,
    set_permission_mode,
    transition_permission_mode,
)

REFERENCE = Path(__file__).resolve().parents[1] / "reference" / "claude-code" / "src"


# ── words.ts ────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not (REFERENCE / "utils" / "words.ts").exists(), reason="reference snapshot absent")
def test_the_word_lists_are_upstreams():
    source = (REFERENCE / "utils" / "words.ts").read_text(encoding="utf-8")

    def listed(name: str) -> tuple[str, ...]:
        block = re.search(rf"const {name} = \[(.*?)\] as const", source, re.DOTALL)
        assert block is not None
        return tuple(re.findall(r"'([^']+)'", block.group(1)))

    assert words.ADJECTIVES == listed("ADJECTIVES")
    assert words.NOUNS == listed("NOUNS")
    assert words.VERBS == listed("VERBS")


def test_a_slug_is_adjective_verb_noun(monkeypatch):
    picks = iter([0, len(words.VERBS) - 1, 1])
    monkeypatch.setattr(words, "random_int", lambda maximum: next(picks))
    assert words.generate_word_slug() == f"{words.ADJECTIVES[0]}-{words.VERBS[-1]}-{words.NOUNS[1]}"


def test_random_int_is_in_range():
    assert {words.random_int(3) for _ in range(200)} <= {0, 1, 2}
    assert words.pick_random(("only",)) == "only"


# ── plans.ts ────────────────────────────────────────────────────────────────


def test_the_plans_directory(tmp_path: Path, caplog):
    root, home = tmp_path / "project", tmp_path / "home"
    root.mkdir()
    assert plans.get_plans_directory({}, root, home) == home / "plans"
    assert (home / "plans").is_dir()
    assert plans.get_plans_directory({"plansDirectory": "docs/plans"}, root, home) == root / "docs" / "plans"
    assert (root / "docs" / "plans").is_dir()
    assert plans.get_plans_directory({"plansDirectory": "."}, root, home) == root
    # Outside the project root: refused, logged, back to the default.
    assert plans.get_plans_directory({"plansDirectory": "../elsewhere"}, root, home) == home / "plans"
    assert "plansDirectory must be within project root: ../elsewhere" in caplog.text
    assert plans.get_plans_directory({"plansDirectory": 3}, root, home) == home / "plans"


def test_a_new_slug_avoids_an_existing_plan_file(tmp_path: Path):
    (tmp_path / "taken.md").write_text("x")
    slugs = iter(["taken", "free"])
    assert plans.new_plan_slug(tmp_path, lambda: next(slugs)) == "free"
    # After MAX_SLUG_RETRIES tries the last one is kept, as upstream.
    assert plans.MAX_SLUG_RETRIES == 10
    assert plans.new_plan_slug(tmp_path, lambda: "taken") == "taken"


def test_the_session_slug_is_saved_and_reused(tmp_path: Path):
    session, root, home = tmp_path / "session", tmp_path / "project", tmp_path / "home"
    root.mkdir()
    first = plans.load_session_plans(session, {}, root, home, generate=lambda: "brave-running-fox")
    assert first == plans.SessionPlans(directory=home / "plans", slug="brave-running-fox")
    assert (session / plans.PLAN_SLUG_FILE_NAME).read_text() == "brave-running-fox"
    again = plans.load_session_plans(session, {}, root, home, generate=lambda: pytest.fail("not regenerated"))
    assert again == first


def test_the_plan_file_its_agent_files_and_reading(tmp_path: Path):
    session_plans = plans.SessionPlans(directory=tmp_path, slug="a-b-c")
    assert session_plans.file_path() == tmp_path / "a-b-c.md"
    assert session_plans.file_path("w1") == tmp_path / "a-b-c-agent-w1.md"
    assert session_plans.read() is None
    session_plans.file_path().write_text("# Plan", encoding="utf-8")
    assert session_plans.read() == "# Plan"
    assert session_plans.is_session_plan_file(str(tmp_path / "a-b-c.md"))
    assert session_plans.is_session_plan_file(str(tmp_path / "a-b-c-agent-w1.md"))
    assert not session_plans.is_session_plan_file(str(tmp_path / "a-b-c.txt"))
    assert not session_plans.is_session_plan_file(str(tmp_path / "other.md"))
    # Normalized first: a traversal out of the directory is not the plan file.
    assert not session_plans.is_session_plan_file(str(tmp_path / "a-b-c" / ".." / ".." / "x.md"))


def test_session_plan_target(tmp_path: Path):
    context = SimpleNamespace(session_plans=plans.SessionPlans(directory=tmp_path, slug="s"))
    assert plans.session_plan_target(context, str(tmp_path / "s.md")) == str(tmp_path / "s.md")
    assert plans.session_plan_target(context, str(tmp_path / "x" / ".." / "s.md")) == str(tmp_path / "s.md")
    assert plans.session_plan_target(context, "s.md") is None  # relative: always the project's
    assert plans.session_plan_target(context, str(tmp_path / "other.md")) is None
    assert plans.session_plan_target(SimpleNamespace(), str(tmp_path / "s.md")) is None


# ── planModeV2.ts ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("value", "agents", "explore"), [
    (None, 1, 3), ("4", 4, 4), ("0", 1, 3), ("11", 1, 3), ("x", 1, 3), ("10", 10, 10)])
def test_the_agent_counts(monkeypatch, value: str | None, agents: int, explore: int):
    for name in (plan_mode_v2.PLAN_V2_AGENT_COUNT_ENV, plan_mode_v2.PLAN_V2_EXPLORE_AGENT_COUNT_ENV):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert plan_mode_v2.get_plan_mode_v2_agent_count() == agents
    assert plan_mode_v2.get_plan_mode_v2_explore_agent_count() == explore


@pytest.mark.parametrize(("value", "enabled"), [(None, False), ("1", True), ("true", True), ("0", False),
                                                ("off", False), ("maybe", False)])
def test_the_interview_phase_switch(monkeypatch, value: str | None, enabled: bool):
    if value is None:
        monkeypatch.delenv(plan_mode_v2.PLAN_MODE_INTERVIEW_PHASE_ENV, raising=False)
    else:
        monkeypatch.setenv(plan_mode_v2.PLAN_MODE_INTERVIEW_PHASE_ENV, value)
    assert plan_mode_v2.is_plan_mode_interview_phase_enabled() is enabled
    assert plan_mode_v2.get_pewter_ledger_variant() is None


# ── permissionSetup.ts / bootstrap/state.ts ─────────────────────────────────


def test_entering_plan_mode_stashes_the_mode():
    context = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
    assert prepare_context_for_plan_mode(context).pre_plan_mode == PermissionMode.ACCEPT_EDITS
    planning = dataclasses.replace(context, mode=PermissionMode.PLAN, pre_plan_mode=PermissionMode.DEFAULT)
    assert prepare_context_for_plan_mode(planning) is planning


def test_the_context_transition():
    accept = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
    assert transition_permission_mode(PermissionMode.ACCEPT_EDITS, PermissionMode.ACCEPT_EDITS, accept) is accept
    entered = transition_permission_mode(PermissionMode.ACCEPT_EDITS, PermissionMode.PLAN, accept)
    assert entered.pre_plan_mode == PermissionMode.ACCEPT_EDITS
    left = transition_permission_mode(PermissionMode.PLAN, PermissionMode.DEFAULT, entered)
    assert left.pre_plan_mode is None
    plain = ToolPermissionContext(mode=PermissionMode.PLAN)
    assert transition_permission_mode(PermissionMode.PLAN, PermissionMode.DEFAULT, plain) is plain
    assert transition_permission_mode(PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS, accept) is accept


def test_the_plan_mode_flags():
    state = AppState(needs_plan_mode_exit_attachment=True)
    entered = handle_plan_mode_transition(state, PermissionMode.DEFAULT, PermissionMode.PLAN)
    assert entered.needs_plan_mode_exit_attachment is False and entered.has_exited_plan_mode is False
    left = handle_plan_mode_transition(AppState(), PermissionMode.PLAN, PermissionMode.ACCEPT_EDITS)
    assert left.needs_plan_mode_exit_attachment is True and left.has_exited_plan_mode is True
    same = AppState()
    assert handle_plan_mode_transition(same, PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS) is same
    assert handle_plan_mode_transition(same, PermissionMode.PLAN, PermissionMode.PLAN) is same


def test_set_permission_mode_round_trip():
    state = AppState(tool_permission_context=ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS))
    planning = set_permission_mode(state, PermissionMode.PLAN)
    context = planning.tool_permission_context
    assert (context.mode, context.pre_plan_mode) == (PermissionMode.PLAN, PermissionMode.ACCEPT_EDITS)
    assert set_permission_mode(planning, PermissionMode.PLAN) is planning
    done = set_permission_mode(planning, PermissionMode.ACCEPT_EDITS)
    assert (done.tool_permission_context.mode, done.tool_permission_context.pre_plan_mode) == (
        PermissionMode.ACCEPT_EDITS, None)
    assert done.needs_plan_mode_exit_attachment and done.has_exited_plan_mode


# ── the Implementation Plan ─────────────────────────────────────────────────


def test_the_heading():
    assert IMPLEMENTATION_PLAN_HEADING == "Implementation Plan"


def test_the_implementation_plan_section_becomes_the_sub_requirements():
    doc = """# Plan: CPF API
## Context
1. not an item

## Implementation Plan
Some intro text.
1. Set up the project
   with pinned dependencies
2. Domain CPF
   - strip the mask
   - 11 digits

3) Route
### Notes inside the section
4. Still an item
## Verification
1. not an item either
"""
    assert parse_implementation_plan(doc) == [
        "Set up the project with pinned dependencies", "Domain CPF - strip the mask - 11 digits", "Route",
        "Still an item"]


def test_text_after_a_sub_heading_does_not_join_the_item_before_it():
    doc = "## Implementation Plan\n1. First\n### Phase two\nIntro of phase two.\n2. Second\n"
    assert parse_implementation_plan(doc) == ["First", "Second"]


def test_nested_numbered_lines_belong_to_their_item():
    doc = "## Implementation Plan\n1. Outer\n   1. inner one\n   2. inner two\n2. Next\n"
    assert parse_implementation_plan(doc) == ["Outer 1. inner one 2. inner two", "Next"]


@pytest.mark.parametrize("heading", ["## Implementation Plan", "# implementation plan", "### IMPLEMENTATION PLAN ###"])
def test_any_heading_level_and_case(heading: str):
    assert parse_implementation_plan(f"{heading}\n1. a\n10. b\n") == ["a", "b"]


@pytest.mark.parametrize("doc", ["", "no section\n1. x", "## Implementation Plan\n", "## Implementation Plans\n1. x"])
def test_no_section_or_no_items(doc: str):
    assert parse_implementation_plan(doc) == []


# ── the gate's carve-out ────────────────────────────────────────────────────


def test_the_session_plan_file_is_writable_and_readable_in_any_mode():
    context = ToolPermissionContext(mode=PermissionMode.PLAN)
    write = check_write_permission_for_tool("/plans/s.md", {"x": 1}, context, "/project", plan_file=True)
    assert write.behavior == PermissionBehavior.ALLOW and write.updated_input == {"x": 1}
    assert write.decision_reason == {"type": "other",
                                     "reason": "Plan files for current session are allowed for writing"}
    read = check_readable_internal_path("/plans/s.md", {}, "/project", plan_file=True)
    assert read.behavior == PermissionBehavior.ALLOW
    assert read.decision_reason == {"type": "other", "reason": "Plan files for current session are allowed for reading"}
    # Anything else in plan mode takes the default path: an ask.
    other = check_write_permission_for_tool("/project/app.py", {}, context, "/project")
    assert other.behavior == PermissionBehavior.ASK


def test_a_deny_rule_still_wins_over_the_plan_file():
    from app.types.permissions import PermissionRule, PermissionRuleSource

    rule = PermissionRule(tool_name="Edit", rule_behavior=PermissionBehavior.DENY, rule_content="//plans/**",
                          source=PermissionRuleSource.USER_SETTINGS)
    context = ToolPermissionContext(mode=PermissionMode.PLAN, always_deny_rules=(rule,))
    result = check_write_permission_for_tool("/plans/s.md", {}, context, "/project", plan_file=True)
    assert result.behavior == PermissionBehavior.DENY


def test_the_phase_tier_lets_the_plan_file_be_written_in_red():
    from app.tdd.ledger import PhaseLedger
    from app.tdd.phase_permissions import check_tdd_phase_permission

    tool: Any = SimpleNamespace(name="WriteFile")
    args = {"file_path": "/plans/s.md", "content": "# Plan"}
    assert check_tdd_phase_permission(tool, args, PhaseLedger(), plan_file=True) == (True, "")
    permitted, _ = check_tdd_phase_permission(tool, args, PhaseLedger())
    assert permitted is False
