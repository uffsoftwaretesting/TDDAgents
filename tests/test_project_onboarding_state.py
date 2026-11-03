"""
The project onboarding (`reference/claude-code/src/projectOnboardingState.ts`) and the tips the
REPL header shows (`components/LogoV2/feedConfigs.tsx` -> `createProjectOnboardingFeed`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

import app.project_onboarding_state as onboarding
from app.utils.config import get_current_project_config, save_current_project_config
from app.utils.file import is_dir_empty

TIP = "Tips for getting started\nRun /init to create a TDDAGENTS.md file with instructions for TDDAgents\n"


def test_is_dir_empty(tmp_path):
    assert is_dir_empty(tmp_path) is True
    assert is_dir_empty(tmp_path / "missing") is True
    (tmp_path / "f").write_text("")
    assert is_dir_empty(tmp_path) is False
    assert is_dir_empty(tmp_path / "f") is False  # not a directory: an error other than ENOENT


def test_an_empty_folder_suggests_creating_an_app(tmp_path):
    steps = onboarding.get_steps(tmp_path)
    assert [(s.key, s.text, s.is_complete, s.is_completable, s.is_enabled) for s in steps] == [
        ("workspace", "Ask TDDAgents to create a new app or clone a repository", False, True, True),
        ("claudemd", "Run /init to create a TDDAGENTS.md file with instructions for TDDAgents", False, True, False),
    ]
    assert onboarding.is_project_onboarding_complete(tmp_path) is False


def test_a_project_without_tddagents_md_suggests_init(tmp_path):
    (tmp_path / "app.py").write_text("")
    workspace, claudemd = onboarding.get_steps(tmp_path)
    assert (workspace.is_enabled, claudemd.is_enabled, claudemd.is_complete) == (False, True, False)
    assert onboarding.is_project_onboarding_complete(tmp_path) is False
    (tmp_path / "TDDAGENTS.md").write_text("")
    assert onboarding.get_steps(tmp_path)[1].is_complete is True
    assert onboarding.is_project_onboarding_complete(tmp_path) is True


def test_maybe_mark_records_completion_and_then_short_circuits(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("")
    onboarding.maybe_mark_project_onboarding_complete(tmp_path)
    assert get_current_project_config(tmp_path) == {}
    (tmp_path / "TDDAGENTS.md").write_text("")
    onboarding.maybe_mark_project_onboarding_complete(tmp_path)
    assert get_current_project_config(tmp_path) == {"hasCompletedProjectOnboarding": True}

    def fail(cwd: Any) -> bool:
        raise AssertionError("the saved flag must short-circuit the filesystem check")

    monkeypatch.setattr(onboarding, "is_project_onboarding_complete", fail)
    onboarding.maybe_mark_project_onboarding_complete(tmp_path)


def test_should_show_project_onboarding(tmp_path, monkeypatch):
    monkeypatch.delenv("IS_DEMO", raising=False)
    (tmp_path / "app.py").write_text("")
    assert onboarding.should_show_project_onboarding(tmp_path) is True
    monkeypatch.setenv("IS_DEMO", "1")
    assert onboarding.should_show_project_onboarding(tmp_path) is False
    monkeypatch.delenv("IS_DEMO")
    for _ in range(3):
        onboarding.increment_project_onboarding_seen_count(tmp_path)
    assert get_current_project_config(tmp_path)["projectOnboardingSeenCount"] == 3
    assert onboarding.should_show_project_onboarding(tmp_path) is True
    onboarding.increment_project_onboarding_seen_count(tmp_path)
    assert onboarding.MAX_PROJECT_ONBOARDING_SEEN_COUNT == 4
    assert onboarding.should_show_project_onboarding(tmp_path) is False


def test_a_completed_onboarding_is_not_shown(tmp_path, monkeypatch):
    monkeypatch.delenv("IS_DEMO", raising=False)
    (tmp_path / "app.py").write_text("")
    save_current_project_config(tmp_path, lambda c: {**c, "hasCompletedProjectOnboarding": True})
    assert onboarding.should_show_project_onboarding(tmp_path) is False
    other = tmp_path / "other"
    other.mkdir()
    (other / "TDDAGENTS.md").write_text("")
    assert onboarding.should_show_project_onboarding(other) is False


def test_a_seen_count_that_is_not_a_number_counts_as_zero(tmp_path):
    save_current_project_config(tmp_path, lambda c: {**c, "projectOnboardingSeenCount": "many"})
    onboarding.increment_project_onboarding_seen_count(tmp_path)
    assert get_current_project_config(tmp_path)["projectOnboardingSeenCount"] == 1


def test_the_feed_lists_enabled_steps_incomplete_first(tmp_path):
    step = onboarding.Step
    steps = [step("a", "Done", True, True, True), step("b", "Off", False, True, False),
             step("c", "Todo", False, True, True)]
    assert onboarding.create_project_onboarding_feed(steps, tmp_path, home=tmp_path / "home") == (
        "Tips for getting started\nTodo\n✔ Done")
    assert onboarding.create_project_onboarding_feed(steps, tmp_path, home=tmp_path) == (
        "Tips for getting started\nTodo\n✔ Done\nNote: You have launched TDDAgents in your home directory. "
        "For the best experience, launch it in a project directory instead.")
    assert onboarding.create_project_onboarding_feed([], Path.home()).endswith(onboarding.HOME_DIRECTORY_WARNING)


def test_the_repl_header_shows_the_tip_once_per_session_and_marks_on_each_turn(
        python_workspace, tmp_path, monkeypatch):
    from app.tdd.plan import TddPlan
    from tests.test_repl import CYCLE, FakeIO, Model, session, until

    monkeypatch.delenv("IS_DEMO", raising=False)
    model = Model([[AIMessage(content=f"reply {i}")] for i in range(4)])

    async def script(repl: Any, io: FakeIO) -> None:
        await until(lambda: not repl.busy and len(model.seen) > 0)
        (tmp_path / "TDDAGENTS.md").write_text("# TDDAGENTS.md\n")
        io.lines.put_nowait("thanks")
        await until(lambda: len(model.seen) == 2 and not repl.busy)
        io.lines.put_nowait("/exit")

    plan = TddPlan.from_texts(["A"]).complete_current(CYCLE)
    _, _, io = asyncio.run(session(python_workspace, tmp_path, model, plan, script))
    assert io.out[0] == TIP
    assert get_current_project_config(tmp_path) == {"projectOnboardingSeenCount": 1,
                                                    "hasCompletedProjectOnboarding": True}

    # Completed: the next session shows no tips.
    model = Model([[AIMessage(content="again")]])

    async def leave(repl: Any, io: FakeIO) -> None:
        await until(lambda: not repl.busy and len(model.seen) > 0)
        io.lines.put_nowait("/exit")

    _, _, io = asyncio.run(session(python_workspace, tmp_path, model, plan, leave))
    assert not any("Tips for getting started" in line for line in io.out)
