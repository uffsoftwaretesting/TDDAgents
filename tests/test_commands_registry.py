"""The command registry (`commands.ts`) and typed `/<skill>` prompt commands (`processSlashCommand`)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.commands import (
    HELP_TEXT,
    Command,
    SlashCommand,
    built_in_command_names,
    find_command,
    format_description_with_source,
    get_command,
    get_commands,
    has_command,
    help_text,
)
from app.skills.load_skills_dir import SkillCommand, SkillRegistry
from app.tdd.plan import TddPlan
from app.utils.process_user_input.process_slash_command import (
    format_command_loading_metadata,
    format_slash_command_loading_metadata,
    get_messages_for_prompt_slash_command,
    looks_like_command,
    parse_slash_input,
    unknown_skill_message,
)


def _skill(name: str = "design", **kw: Any) -> SkillCommand:
    kw.setdefault("source", "projectSettings")
    kw.setdefault("loaded_from", "skills")
    kw.setdefault("skill_root", "/skills/" + name)
    return SkillCommand(name=name, description=kw.pop("description", "Design tests"),
                        markdown_content=kw.pop("body", "Design tests for $ARGUMENTS."), **kw)


def test_the_registry_lists_skills_then_built_ins():
    commands = get_commands(SkillRegistry([_skill(), _skill("hidden", user_invocable=False)]))
    assert [(c.name, c.type) for c in commands][:3] == [("design", "prompt"), ("hidden", "prompt"), ("plan", "local")]
    local = {c.name for c in commands if c.type == "local"}
    assert local == {"plan", "pause", "resume", "requirements", "context", "help", "exit"}
    assert get_commands(None) == get_commands(SkillRegistry([]))
    assert built_in_command_names() == {
        "plan", "pause", "resume", "requirements", "context", "help", "exit", "quit"}


def test_find_has_and_get_command():
    commands = [*get_commands(None), Command("x", "d", "prompt", display_name="Shown")]
    assert find_command("quit", commands).name == "exit"  # type: ignore[union-attr]
    assert find_command("Shown", commands).name == "x"  # type: ignore[union-attr]
    assert has_command("plan", commands) and not has_command("nope", commands)
    assert get_command("help", commands).name == "help"
    message = (r"^Command nope not found\. Available commands: Shown, context, exit \(aliases: "
               r"quit\), help, pause, plan, requirements, resume$")
    with pytest.raises(LookupError, match=message):
        get_command("nope", commands)


@pytest.mark.parametrize("source, expected", [
    ("bundled", "D (bundled)"), ("userSettings", "D (user)"), ("projectSettings", "D (project)"),
    ("policySettings", "D (managed)"), ("localSettings", "D (project, gitignored)"), ("odd", "D (odd)"),
    ("builtin", "D"), ("mcp", "D"),
])
def test_format_description_with_source(source, expected):
    assert format_description_with_source(Command("x", "D", "prompt", source=source)) == expected
    assert format_description_with_source(Command("x", "D", "local", source=source)) == "D"


def test_help_lists_the_skills_a_user_can_type():
    assert help_text(get_commands(None)) == HELP_TEXT
    text = help_text(get_commands(SkillRegistry([_skill(), _skill("hidden", user_invocable=False),
                                                 _skill("tdd-x", source="bundled", description="Bundled")])))
    assert text.startswith(HELP_TEXT + "\nSkills:\n")
    assert "  /design   Design tests (project)" in text and "  /tdd-x    Bundled (bundled)" in text
    assert "hidden" not in text


def test_a_prompt_command_is_never_immediate():
    assert SlashCommand("help").immediate and not SlashCommand("help", kind="prompt").immediate


def test_parsing_and_looks_like_command():
    assert parse_slash_input("/design  the calc  ") == ("design", "the calc")
    assert parse_slash_input("/design") == ("design", "")
    assert [parse_slash_input(t) for t in ("design", "/", "  ")] == [None, None, None]
    assert looks_like_command("tdd-test_design:x") and not looks_like_command("usr/bin")
    assert unknown_skill_message("nope", "") == "Unknown skill: nope"
    assert unknown_skill_message("nope", "a b") == "Unknown skill: nope\nArgs from unknown skill: a b"


def test_loading_metadata():
    assert format_slash_command_loading_metadata("d") == "<command-message>d</command-message>\n" \
                                                         "<command-name>/d</command-name>"
    assert format_slash_command_loading_metadata("d", "x y").endswith("\n<command-args>x y</command-args>")
    assert format_command_loading_metadata(_skill(), "a").startswith("<command-message>design</command-message>\n"
                                                                     "<command-name>/design</command-name>")
    model_only = _skill(user_invocable=False)
    assert format_command_loading_metadata(model_only) == ("<command-message>design</command-message>\n"
                                                           "<command-name>design</command-name>\n"
                                                           "<skill-format>true</skill-format>")
    bundled_model_only = _skill(user_invocable=False, loaded_from="bundled")
    assert format_command_loading_metadata(bundled_model_only).endswith("<command-name>/design</command-name>")


def test_a_prompt_command_turn():
    skill = _skill(allowed_tools=("Bash(make:*)", "ReadFile"), model="haiku")
    turn = get_messages_for_prompt_slash_command(skill, "the calculator", session_id="s1")
    metadata, content = turn.messages
    assert str(metadata.content).endswith("<command-args>the calculator</command-args>")
    assert content.additional_kwargs == {"is_meta": True}
    assert "Design tests for the calculator." in content.content
    assert (turn.allowed_tools, turn.model) == (("Bash(make:*)", "ReadFile"), "haiku")
    coordinator = get_messages_for_prompt_slash_command(skill, "", coordinator=True)
    assert str(coordinator.messages[1].content).startswith('Skill "/design" is available for workers.')
    assert coordinator.allowed_tools == ()


# ── the REPL ─────────────────────────────────────────────────────────────────

def test_a_typed_skill_loads_it_into_the_turn_and_unknown_ones_are_answered(python_workspace, tmp_path):
    from tests.test_repl import FakeIO, Model, session, until

    skill_dir = tmp_path / ".tddagents" / "skills" / "greet"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\ndescription: Say hello\nallowed-tools: ReadFile\n---\nGreet $ARGUMENTS.")
    model = Model([[AIMessage(content=f"reply {i}")] for i in range(6)])

    async def script(repl: Any, io: FakeIO) -> None:
        await until(lambda: not repl.busy and len(model.seen) > 0)
        start = len(model.seen)
        io.lines.put_nowait("/nope x")
        await until(lambda: "Unknown skill: nope\nArgs from unknown skill: x" in io.text())
        assert len(model.seen) == start  # never sent
        io.lines.put_nowait("/usr/bin")  # a path, not a command: a message to the agents
        await until(lambda: len(model.seen) == start + 1 and not repl.busy)
        io.lines.put_nowait("/help")
        await until(lambda: re.search(r"\n  /greet +Say hello \(project\)", io.text()) is not None)
        io.lines.put_nowait("/greet Ana")
        await until(lambda: len(model.seen) == start + 2 and not repl.busy)
        io.lines.put_nowait("/exit")

    from tests.test_repl import CYCLE

    asyncio.run(session(python_workspace, tmp_path, model, TddPlan.from_texts(["A"]).complete_current(CYCLE),
                        script))
    last = model.seen[-1].messages
    said = [m for m in last if isinstance(m, HumanMessage)]
    assert any(str(m.content).endswith("<command-name>/greet</command-name>\n<command-args>Ana</command-args>")
               for m in said)
    assert any("Greet Ana." in str(m.content) and m.additional_kwargs.get("is_meta") for m in said)
    assert any(m.content == "/usr/bin" for m in said)
