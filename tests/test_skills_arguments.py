"""Phase 8: skill argument substitution (`utils/argumentSubstitution.ts`) and the prompt a
skill command becomes (`createSkillCommand` -> `getPromptForCommand`)."""

from __future__ import annotations

from app.loop.skills.arguments import (
    parse_argument_names,
    parse_arguments,
    substitute_arguments,
)
from app.loop.skills.definition import SkillCommand


def test_parse_arguments_is_shell_like_with_a_whitespace_fallback():
    assert parse_arguments("") == []
    assert parse_arguments("   ") == []
    assert parse_arguments('a "b c" d') == ["a", "b c", "d"]
    assert parse_arguments('unclosed "quote here') == ["unclosed", '"quote', "here"]


def test_argument_names():
    assert parse_argument_names(None) == []
    assert parse_argument_names("x 3 y") == ["x", "y"]
    assert parse_argument_names(["a", "", "7"]) == ["a"]


def test_no_args_leaves_content_untouched():
    assert substitute_arguments("keep $ARGUMENTS", None) == "keep $ARGUMENTS"


def test_named_indexed_and_whole_substitution():
    content = "$file then $ARGUMENTS[1] and $0, all: $ARGUMENTS; $filex stays"
    out = substitute_arguments(content, "a.py b.py", argument_names=["file"])
    assert out == "a.py then b.py and a.py, all: a.py b.py; $filex stays"


def test_missing_positions_become_empty():
    assert substitute_arguments("[$2][$ARGUMENTS[5]][$name]", "one", argument_names=["", "name"]) == "[][][]"


def test_args_are_appended_when_nothing_matched():
    assert substitute_arguments("plain", "x y") == "plain\n\nARGUMENTS: x y"
    assert substitute_arguments("plain", "x", append_if_no_placeholder=False) == "plain"
    assert substitute_arguments("plain", "") == "plain"


def _cmd(**kw) -> SkillCommand:
    base = {"name": "s", "description": "d",
            "markdown_content": "Do $ARGUMENTS in ${TDDAGENTS_SKILL_DIR} for ${TDDAGENTS_SESSION_ID}",
            "source": "userSettings", "loaded_from": "skills", "skill_root": "/sk"}
    base.update(kw)
    return SkillCommand(**base)  # type: ignore[arg-type]


def test_the_prompt_for_a_skill():
    cmd = _cmd()
    assert cmd.prompt_for_command("it", session_id="run-1") == (
        "Base directory for this skill: /sk\n\nDo it in /sk for run-1")


def test_a_skill_without_a_root_has_no_base_dir_line():
    cmd = _cmd(skill_root=None, markdown_content="x $ARGUMENTS ${TDDAGENTS_SKILL_DIR}")
    assert cmd.prompt_for_command("a") == "x a ${TDDAGENTS_SKILL_DIR}"


def test_command_properties():
    cmd = _cmd(display_name="Nice", user_invocable=False)
    assert (cmd.user_facing_name(), cmd.is_hidden, cmd.content_length) == ("Nice", True, len(cmd.markdown_content))
    plain = _cmd()
    assert (plain.user_facing_name(), plain.is_hidden) == ("s", False)
