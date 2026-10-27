"""`preparePermissionMatcher` on the tools (`BuiltTool`, fs tools, Bash)."""

from __future__ import annotations

from typing import Any

from app.tool import ToolResult, build_tool
from app.tools.bash_tool.bash_tool import bash_permission_matcher, build_bash_tool
from app.tools.file_tools import (
    build_edit_tool,
    build_glob_tool,
    build_grep_tool,
    build_read_file_tool,
    build_write_file_tool,
    file_path_permission_matcher,
    search_pattern_permission_matcher,
)


async def _call(args: dict[str, Any], ctx: Any) -> ToolResult:  # pragma: no cover - never called
    return ToolResult(content="")


def test_builtin_tool_without_a_matcher_returns_none():
    tool = build_tool(name="T", prompt="p", call=_call)
    assert tool.prepare_permission_matcher({"x": 1}) is None


def test_builtin_tool_with_a_matcher_passes_the_input():
    seen: list[dict[str, Any]] = []

    def prepare(args: dict[str, Any]) -> Any:
        seen.append(args)
        return lambda content: content == "yes"

    tool = build_tool(name="T", prompt="p", call=_call, prepare_permission_matcher=prepare)
    matcher = tool.prepare_permission_matcher({"x": 1})
    assert seen == [{"x": 1}]
    assert matcher is not None and matcher("yes") is True and matcher("no") is False


def test_tools_are_wired_to_their_matchers():
    for build in (build_read_file_tool, build_write_file_tool, build_edit_tool):
        m = build().prepare_permission_matcher({"file_path": "src/app.py"})
        assert m is not None and m("src/*") is True and m("tests/*") is False
    for build in (build_glob_tool, build_grep_tool):
        m = build().prepare_permission_matcher({"pattern": "**/*.py"})
        assert m is not None and m("**/*.py") is True and m("*.js") is False
    m = build_bash_tool().prepare_permission_matcher({"command": "git push"})
    assert m is not None and m("git *") is True and m("npm *") is True  # external build: always


def test_file_path_matcher():
    m = file_path_permission_matcher({"file_path": "src/app.py"})
    assert m("src/*") is True
    assert m("src/app.py") is True
    assert m("*.py") is True
    assert m("tests/*") is False
    missing = file_path_permission_matcher({})
    assert missing("") is True
    assert missing("x") is False


def test_search_pattern_matcher():
    m = search_pattern_permission_matcher({"pattern": "TODO"})
    assert m("TODO") is True
    assert m("TO*") is True
    assert m("FIXME") is False
    assert search_pattern_permission_matcher({})("") is True


def test_bash_matches_every_condition_in_the_external_build():
    """
    `BashTool.preparePermissionMatcher` -> `parseForSecurity` is `parse-unavailable` without
    `TREE_SITTER_BASH`, and the matcher fails safe: every content condition runs the hook.
    """
    for command in ("git push origin", "ls -la && git push", "FOO=1 make", 'echo "unterminated', "rm -rf x"):
        m = bash_permission_matcher({"command": command})
        for pattern in ("git *", "git:*", "npm *", "rm:*", "", "anything at all"):
            assert m(pattern) is True


def test_bash_missing_command_never_reaches_the_matcher():
    """A schema-failing input gets no matcher at all (`prepareIfConditionMatcher`), so only a
    bare `Bash` condition matches it; the matcher itself is input-independent."""
    from app.utils.hooks.hooks import prepare_if_condition_matcher

    matcher = prepare_if_condition_matcher(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}, [build_bash_tool()]
    )
    assert matcher is not None
    assert matcher("Bash") is True
    assert matcher("Bash(git *)") is False
