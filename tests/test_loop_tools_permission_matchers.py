"""`preparePermissionMatcher` on the tools (`BuiltTool`, fs tools, Bash)."""

from __future__ import annotations

from typing import Any

from app.loop.tools.base import build_tool
from app.loop.tools.bash import bash_permission_matcher, build_bash_tool
from app.loop.tools.fs import (
    build_edit_tool,
    build_glob_tool,
    build_grep_tool,
    build_read_file_tool,
    build_write_file_tool,
    file_path_permission_matcher,
    search_pattern_permission_matcher,
)
from app.loop.tools.types import ToolResult


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
    assert m is not None and m("git *") is True and m("npm *") is False


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


def test_bash_wildcard_and_prefix():
    m = bash_permission_matcher({"command": "git push origin"})
    assert m("git *") is True
    assert m("git:*") is True
    assert m("git push:*") is True
    assert m("git pull:*") is False
    assert m("gi:*") is False
    assert m("npm *") is False
    assert m("git push origin") is True
    assert m("git push") is False


def test_bash_prefix_matches_the_bare_command():
    assert bash_permission_matcher({"command": "git"})("git:*") is True
    assert bash_permission_matcher({"command": "gitk"})("git:*") is False


def test_bash_any_subcommand_matches():
    m = bash_permission_matcher({"command": "ls -la && git push"})
    assert m("git *") is True
    assert m("ls *") is True
    assert m("rm *") is False
    piped = bash_permission_matcher({"command": "cat f | grep x; echo done"})
    assert piped("grep *") is True and piped("echo:*") is True


def test_bash_env_assignments_are_stripped():
    m = bash_permission_matcher({"command": "FOO=bar BAZ=1 git push"})
    assert m("git *") is True
    assert m("FOO=bar *") is False
    assert bash_permission_matcher({"command": "_X9=1 make"})("make:*") is True
    # Not an assignment: kept as argv[0].
    assert bash_permission_matcher({"command": "9X=1 make"})("make:*") is False


def test_bash_argv_join_drops_quotes():
    m = bash_permission_matcher({"command": 'git commit -m "a message"'})
    assert m("git commit -m a message") is True


def test_bash_unparseable_runs_the_hook():
    m = bash_permission_matcher({"command": 'echo "unterminated'})
    assert m("anything") is True
    assert m("") is True


def test_bash_missing_command():
    m = bash_permission_matcher({})
    assert m("git *") is False
