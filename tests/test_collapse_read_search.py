"""The REPL's read/search collapse and its helpers (Part 8b)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.components.messages.collapsed_read_search_content import (
    CollapsedReadSearchRenderer,
    response_lines,
)
from app.state.app_state_store import AppStateStore
from app.tool import (
    SearchOrRead,
    ToolResult,
    build_tool,
    default_map_result,
    tool_context_for,
)
from app.tools.bash_tool import ui as bash_ui
from app.tools.bash_tool.bash_tool import build_bash_tool
from app.tools.bash_tool.comment_label import extract_bash_comment_label
from app.tools.file_edit_tool.file_edit_tool import build_edit_tool
from app.tools.file_read_tool import ui as read_ui
from app.tools.file_read_tool.file_read_tool import build_read_file_tool
from app.tools.glob_tool import ui as glob_ui
from app.tools.glob_tool.glob_tool import build_glob_tool
from app.tools.grep_tool import ui as grep_ui
from app.tools.grep_tool.grep_tool import build_grep_tool
from app.tools.mcp_tool import ui as mcp_ui
from app.tools.shared.git_operation_tracking import (
    BranchOp,
    Commit,
    PullRequest,
    Push,
    detect_git_operation,
    parse_ref_from_command,
)
from app.utils.collapse_read_search import (
    MAX_HINT_CHARS,
    ReadSearchGroup,
    SearchOrReadResult,
    collapsed_summary_text,
    command_as_hint,
    get_search_read_summary_text,
    get_tool_search_or_read_info,
    is_memory_search,
    is_memory_write_or_edit,
    summarize_recent_activities,
)
from app.utils.fullscreen import is_fullscreen_env_enabled
from app.utils.memory_file_detection import (
    detect_session_file_type,
    detect_session_pattern_type,
    is_auto_managed_memory_file,
    is_auto_managed_memory_pattern,
    is_memory_directory,
    is_shell_command_targeting_memory,
)
from app.utils.messages import ATTACHMENT_KEY
from app.workspace.local import LocalWorkspace

TOOLS = (build_read_file_tool(), build_grep_tool(), build_glob_tool(), build_bash_tool(), build_edit_tool())


@pytest.fixture(autouse=True)
def no_fullscreen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TDDAGENTS_NO_FLICKER", raising=False)


def _call(name: str, call_id: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "id": call_id, "args": args}


def _ctx(root: Path) -> Any:
    return tool_context_for(AppStateStore(), workspace=LocalWorkspace(root), run_id="r1")


# ── small helpers ────────────────────────────────────────────────────────────

def test_command_as_hint():
    assert command_as_hint("ls   -la\n\n  cat  a  ") == "$ ls -la\ncat a"
    long = "x" * 400
    assert command_as_hint(long) == ("$ " + long)[: MAX_HINT_CHARS - 1] + "…"
    assert len(command_as_hint("y" * 298)) == 300


def test_fullscreen_is_an_explicit_opt_in():
    for value, expected in ((None, False), ("1", True), ("true", True), ("0", False), ("off", False),
                            ("maybe", False)):
        env = {} if value is None else {"TDDAGENTS_NO_FLICKER": value}
        assert is_fullscreen_env_enabled(env) is expected


def test_the_bash_comment_label():
    assert extract_bash_comment_label("# Run the tests\npytest") == "Run the tests"
    assert extract_bash_comment_label("  ## spaced  ") == "spaced"
    assert extract_bash_comment_label("#!/bin/bash\nls") is None
    assert extract_bash_comment_label("ls # not first") is None
    assert extract_bash_comment_label("#") is None


# ── gitOperationTracking ─────────────────────────────────────────────────────

def test_git_operations_from_command_and_output():
    commit = detect_git_operation("git -c x=y commit -m m", "[main 1a2b3c4d] msg")
    assert commit.commit == Commit(sha="1a2b3c", kind="committed")
    assert detect_git_operation("git commit --amend", "[main (root-commit) abcdef9] m").commit == Commit(
        sha="abcdef", kind="amended")
    picked = detect_git_operation("git cherry-pick x", "[dev 0123456] m")
    assert picked.commit == Commit(sha="012345", kind="cherry-picked")
    assert detect_git_operation("git log", "[main 1a2b3c4] msg").commit is None
    push = detect_git_operation("git push origin dev", " * [new branch]      dev -> dev\n")
    assert push.push == Push(branch="dev")
    assert detect_git_operation("git push", "abc..def  main -> main").push == Push(branch="main")
    merged = detect_git_operation("git merge --no-ff feature && echo", "Merge made by the 'ort' strategy.")
    assert merged.branch == BranchOp(ref="feature", action="merged")
    assert detect_git_operation("git merge-base a b", "Fast-forward").branch is None
    rebased = detect_git_operation("git rebase main", "Successfully rebased and updated refs/heads/x.")
    assert rebased.branch == BranchOp(ref="main", action="rebased")
    pr = detect_git_operation("gh pr create --fill", "https://github.com/o/r/pull/42\n")
    assert pr.pr == PullRequest(number=42, action="created", url="https://github.com/o/r/pull/42")
    closed = detect_git_operation("gh pr close 7", "✓ Closed pull request o/r#7")
    assert closed.pr == PullRequest(number=7, action="closed")
    assert detect_git_operation("gh pr view", "pull request #3").pr is None
    assert parse_ref_from_command("git merge -q", "merge") is None
    assert parse_ref_from_command("git merge | tee x", "merge") is None
    assert parse_ref_from_command("git status", "merge") is None


# ── memoryFileDetection ──────────────────────────────────────────────────────

def test_memory_detection_with_auto_memory_on(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_ENABLE_AUTO_MEMORY", "1")
    from app.utils.memory_file_detection import auto_memory_dir
    from app.utils.tool_result_storage import config_home

    ctx = _ctx(tmp_path)
    memory_dir = auto_memory_dir(ctx)
    assert memory_dir is not None
    config = str(config_home())
    assert is_auto_managed_memory_file(ctx, f"{memory_dir}notes.md")
    assert is_auto_managed_memory_file(ctx, f"{config}/projects/x/session.jsonl")
    assert not is_auto_managed_memory_file(ctx, str(tmp_path / "TDDAGENTS.md"))
    assert is_memory_directory(ctx, "/x/agent-memory/tester")
    assert is_memory_directory(ctx, memory_dir.rstrip("/"))
    assert is_memory_directory(ctx, f"{config}/session-memory/s")
    assert is_memory_directory(ctx, f"{config}/projects/p")
    assert is_memory_directory(ctx, f"{config}/other/memory/x")
    assert not is_memory_directory(ctx, f"{config}/other")
    assert not is_memory_directory(ctx, "/elsewhere")
    assert is_shell_command_targeting_memory(ctx, f"grep -r todo {memory_dir}notes.md;")
    assert not is_shell_command_targeting_memory(ctx, "grep -r todo /etc")
    assert not is_shell_command_targeting_memory(ctx, f"echo {config}x")
    assert is_auto_managed_memory_pattern(ctx, "**/agent-memory/**")
    assert is_auto_managed_memory_pattern(ctx, "*.jsonl")
    assert not is_auto_managed_memory_pattern(ctx, "*.py")


def test_memory_detection_with_auto_memory_off(tmp_path, monkeypatch):
    monkeypatch.delenv("TDDAGENTS_ENABLE_AUTO_MEMORY", raising=False)
    ctx = _ctx(tmp_path)
    assert not is_memory_directory(ctx, "/x/agent-memory/tester")
    assert not is_auto_managed_memory_pattern(ctx, "**/agent-memory/**")
    from app.utils.tool_result_storage import config_home

    assert not is_memory_directory(ctx, f"{config_home()}/x/memory/y")


def test_session_file_types():
    from app.utils.tool_result_storage import config_home

    config = str(config_home())
    assert detect_session_file_type(f"{config}/a/session-memory/b.md") == "session_memory"
    assert detect_session_file_type(f"{config}/projects/p/s.jsonl") == "session_transcript"
    assert detect_session_file_type(f"{config}/projects/p/s.md") is None
    assert detect_session_file_type("/other/session-memory/b.md") is None
    assert detect_session_pattern_type("session-memory/*.md") == "session_memory"
    assert detect_session_pattern_type("session-memory/*") == "session_memory"
    assert detect_session_pattern_type("projects/*.jsonl") == "session_transcript"
    assert detect_session_pattern_type("*.py") is None


# ── getToolSearchOrReadInfo ──────────────────────────────────────────────────

def test_which_tool_uses_collapse(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    info = get_tool_search_or_read_info
    assert info("ReadFile", {"file_path": "a"}, TOOLS, ctx) == SearchOrReadResult(True, is_read=True)
    assert info("Grep", {"pattern": "x"}, TOOLS, ctx) == SearchOrReadResult(True, is_search=True)
    assert info("Bash", {"command": "ls"}, TOOLS, ctx) == SearchOrReadResult(True, is_list=True)
    assert info("Bash", {"command": "npm test"}, TOOLS, ctx).is_collapsible is False
    assert info("Edit", {"file_path": "a"}, TOOLS, ctx).is_collapsible is False
    assert info("Nope", {}, TOOLS, ctx).is_collapsible is False
    monkeypatch.setenv("TDDAGENTS_NO_FLICKER", "1")
    bash = info("Bash", {"command": "npm test"}, TOOLS, ctx)
    assert (bash.is_collapsible, bash.is_bash) == (True, True)
    assert info("Bash", {"command": "ls"}, TOOLS, ctx).is_bash is False


def test_mcp_tools_carry_their_server(tmp_path):
    mcp = build_tool(name="mcp__slack__slack_search_public", prompt="p", call=lambda a, c: None,  # type: ignore
                     is_mcp=True, is_search_or_read_command=lambda a: SearchOrRead(True, False))
    from app.tools.mcp_tool.mcp_tool import McpBuiltTool

    built = McpBuiltTool(**{f: getattr(mcp, f) for f in type(mcp).__dataclass_fields__}, server_name="slack",
                         mcp_tool_name="slack_search_public")
    result = get_tool_search_or_read_info(built.name, {"query": "x"}, (built,), _ctx(tmp_path))
    assert (result.is_collapsible, result.mcp_server_name) == (True, "slack")


def test_memory_writes_collapse(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_ENABLE_AUTO_MEMORY", "1")
    from app.utils.memory_file_detection import auto_memory_dir

    ctx = _ctx(tmp_path)
    memory_file = f"{auto_memory_dir(ctx)}user.md"
    assert is_memory_write_or_edit(ctx, "WriteFile", {"file_path": memory_file})
    assert is_memory_write_or_edit(ctx, "Edit", {"path": memory_file})
    assert not is_memory_write_or_edit(ctx, "ReadFile", {"file_path": memory_file})
    assert not is_memory_write_or_edit(ctx, "WriteFile", {})
    write = get_tool_search_or_read_info("WriteFile", {"file_path": memory_file}, TOOLS, ctx)
    assert (write.is_collapsible, write.is_memory_write) == (True, True)
    assert is_memory_search(ctx, {"path": memory_file})
    assert is_memory_search(ctx, {"glob": "**/agent-memory/*"})
    assert not is_memory_search(ctx, {"pattern": "x"})


# ── the group and its summary ────────────────────────────────────────────────

def _group(tmp_path: Path, *calls: dict[str, Any]) -> ReadSearchGroup:
    ctx = _ctx(tmp_path)
    group = ReadSearchGroup(context=ctx)
    for call in calls:
        group.add_tool_use(call, get_tool_search_or_read_info(call["name"], call["args"], TOOLS, ctx))
    return group


def test_reads_count_unique_files_and_searches_patterns(tmp_path):
    group = _group(tmp_path, _call("ReadFile", "1", file_path="a.py"), _call("ReadFile", "2", file_path="a.py"),
                   _call("Bash", "3", command="wc -l a.py"), _call("Grep", "4", pattern="def x"),
                   _call("Glob", "5", pattern="*.md"), _call("ReadFile", "6", file_path="b.py"))
    assert (group.read_count, group.collapsed_search_count) == (2, 2)
    assert collapsed_summary_text(group) == "Searched for 2 patterns, read 2 files"
    assert collapsed_summary_text(group, active=True) == "Searching for 2 patterns, reading 2 files…"
    assert group.latest_display_hint == "b.py" and group.owns("3") and not group.owns("9")


def test_bash_reads_count_operations_and_lists_directories(tmp_path):
    group = _group(tmp_path, _call("Bash", "1", command="cat a"), _call("Bash", "2", command="ls src"),
                   _call("Bash", "3", command="tree"))
    assert collapsed_summary_text(group) == "Read 1 file, listed 2 directories"
    assert group.displayed_hint == "$ tree"
    one = _group(tmp_path, _call("Grep", "1", pattern="x"))
    assert collapsed_summary_text(one) == "Searched for 1 pattern"
    assert one.displayed_hint == '"x"'


def test_the_hint_falls_back_to_the_last_read_or_search(tmp_path):
    group = ReadSearchGroup(context=_ctx(tmp_path))
    assert group.displayed_hint is None and group.is_empty and collapsed_summary_text(group) == ""
    group.read_file_paths.append("z.py")
    assert group.displayed_hint == "z.py"
    other = ReadSearchGroup(context=_ctx(tmp_path), non_mem_search_args=["p"])
    assert other.displayed_hint == '"p"'


def test_memory_operations_follow_the_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_ENABLE_AUTO_MEMORY", "1")
    from app.utils.memory_file_detection import auto_memory_dir

    ctx = _ctx(tmp_path)
    memory = auto_memory_dir(ctx)
    group = _group(tmp_path, _call("ReadFile", "1", file_path=f"{memory}a.md"),
                   _call("Grep", "2", pattern="x", path=f"{memory}"),
                   _call("WriteFile", "3", file_path=f"{memory}b.md", content="c"))
    assert collapsed_summary_text(group) == "Recalled 1 memory, searched memories, wrote 1 memory"
    group.absorb_relevant_memories([{"path": "m.md", "content": "c"}])
    with_read = _group(tmp_path, _call("ReadFile", "4", file_path="x.py"))
    with_read.absorb_relevant_memories([{"path": "m.md"}, {"path": "n.md"}])
    assert collapsed_summary_text(with_read, active=True) == "Reading 1 file, recalling 2 memories…"
    assert collapsed_summary_text(group) == "Recalled 2 memories, searched memories, wrote 1 memory"


def test_mcp_queries_name_their_servers(tmp_path):
    group = ReadSearchGroup(context=_ctx(tmp_path))
    info = SearchOrReadResult(True, is_search=True, mcp_server_name="claude.ai Slack")
    group.add_tool_use(_call("mcp__x__s", "1", query="deploy"), info)
    assert collapsed_summary_text(group) == "Queried Slack"
    assert group.latest_display_hint == '"deploy"'
    group.add_tool_use(_call("mcp__x__s", "2"), info)
    group.add_tool_use(_call("mcp__y__t", "3"), SearchOrReadResult(True, is_read=True, mcp_server_name="github"))
    assert collapsed_summary_text(group, active=True) == "Querying Slack, github 3 times…"
    silent = ReadSearchGroup(context=_ctx(tmp_path))
    silent.add_tool_use(_call("Snip", "1"), SearchOrReadResult(True, is_absorbed_silently=True))
    assert collapsed_summary_text(silent) == "" and not silent.is_empty


def test_fullscreen_counts_bash_and_surfaces_git_operations(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_NO_FLICKER", "1")
    group = _group(tmp_path, _call("Bash", "1", command="# Commit it\ngit commit -m x"),
                   _call("Bash", "2", command="git push"), _call("Bash", "3", command="npm test"),
                   _call("Bash", "4", command="gh pr create"), _call("Bash", "5", command="git rebase main"),
                   _call("Bash", "6", command="git merge dev"), _call("Grep", "7", pattern="x"))
    assert group.bash_commands["1"].startswith("# Commit")
    group.absorb_result("1", {"stdout": "[main abc1234] x"})
    group.absorb_result("2", {"stdout": "", "stderr": "abc..def  main -> main"})
    group.absorb_result("3", {"stdout": "", "stderr": ""})
    group.absorb_result("4", {"stdout": "https://github.com/o/r/pull/9"})
    group.absorb_result("5", {"stdout": "Successfully rebased"})
    group.absorb_result("6", {"stdout": "Fast-forward"})
    group.absorb_result("7", None)
    assert collapsed_summary_text(group) == (
        "Committed abc123, pushed to main, rebased onto main, merged dev, created PR #9, searched for 1 pattern, "
        "ran 1 bash command")
    amended = _group(tmp_path, _call("Bash", "1", command="git commit --amend"),
                     _call("Bash", "2", command="git cherry-pick a"), _call("Bash", "3", command="gh pr edit 1"),
                     _call("Bash", "4", command="ls"))
    amended.absorb_result("1", {"stdout": "[main 1111111] x"})
    amended.absorb_result("2", {"stdout": "[main 2222222] x"})
    amended.absorb_result("3", {"stdout": "Edited pull request o/r#1"})
    assert collapsed_summary_text(amended, active=True) == (
        "Amended commit 111111, cherry-picked 222222, edited PR #1, listing 1 directory…")
    assert amended.latest_display_hint == "$ ls"
    plain = _group(tmp_path, _call("Bash", "1", command="echo hi"))
    plain.absorb_result("1", "not a mapping")
    assert collapsed_summary_text(plain) == "Ran 1 bash command" and plain.latest_display_hint == "$ echo hi"
    monkeypatch.delenv("TDDAGENTS_NO_FLICKER")
    assert collapsed_summary_text(amended) == "Listed 1 directory"


def test_search_read_summary_text_and_recent_activities():
    text = get_search_read_summary_text
    assert text(2, 1, False) == "Searched for 2 patterns, read 1 file"
    assert text(1, 0, True, repl_count=2) == "Searching for 1 pattern, REPL'ing 2 times…"
    assert text(0, 0, False, repl_count=1) == "REPL'd 1 time"
    memory = {"memoryReadCount": 1, "memorySearchCount": 2, "memoryWriteCount": 3}
    assert text(1, 0, False, memory_counts=memory, list_count=2) == (
        "Recalled 1 memory, searched memories, wrote 3 memories, searched for 1 pattern, listed 2 directories")
    assert text(0, 2, True, memory_counts={"memorySearchCount": 1}) == "Searching memories, reading 2 files…"
    assert summarize_recent_activities([]) is None
    assert summarize_recent_activities([{"activityDescription": "Editing a"}, {"isSearch": True},
                                        {"isRead": True}]) == "Searching for 1 pattern, reading 1 file…"
    assert summarize_recent_activities([{"activityDescription": "Editing a"}, {"isRead": True}]) == "Editing a"
    assert summarize_recent_activities([{"isRead": True}]) is None


# ── renderToolResultMessage ──────────────────────────────────────────────────

def test_read_results():
    render = read_ui.render_tool_result_message
    assert render({"type": "text", "file": {"numLines": 1}}, True) == "Read 1 line"
    assert render({"type": "text", "file": {"numLines": 12}}, True) == "Read 12 lines"
    assert render({"type": "image", "file": {"originalSize": 2048}}, True) == "Read image (2KB)"
    assert render({"type": "notebook", "file": {"cells": [1, 2]}}, True) == "Read 2 cells"
    assert render({"type": "notebook", "file": {"cells": []}}, True) == "No cells found in notebook"
    assert render({"type": "pdf", "file": {"originalSize": 1024}}, True) == "Read PDF (1KB)"
    assert render({"type": "parts", "file": {"count": 1, "originalSize": 10}}, True) == "Read 1 page (10 bytes)"
    assert render({"type": "parts", "file": {"count": 3, "originalSize": 10}}, True) == "Read 3 pages (10 bytes)"
    assert render({"type": "file_unchanged"}, True) == "Unchanged since last read"
    assert render({"type": "zip"}, True) is None and render("text", True) is None


def test_search_results():
    render = grep_ui.render_tool_result_message
    assert render({"mode": "files_with_matches", "numFiles": 2, "filenames": ["a", "b"]}, True) == "Found 2 files\na\nb"
    assert render({"numFiles": 1, "filenames": ["a"]}, False) == "Found 1 file"
    assert render({"numFiles": 0, "filenames": []}, True) == "Found 0 files"
    assert render({"mode": "content", "numLines": 1, "content": "a:1:x"}, True) == "Found 1 line\na:1:x"
    assert render({"mode": "count", "numMatches": 5, "numFiles": 1, "content": "a:5"}, True) == (
        "Found 5 matches across 1 file\na:5")
    # Upstream's `countLabel.slice(0, -1)` singularizes "matches" to "matche"; kept as it renders.
    assert render({"mode": "count", "numMatches": 1, "numFiles": 0}, True) == "Found 1 matche across 0 files"
    assert glob_ui.render_tool_result_message({"numFiles": 1, "filenames": ["x"]}, True) == "Found 1 file\nx"
    assert render(None, True) is None


def test_bash_results():
    render = bash_ui.render_tool_result_message
    assert render({"stdout": "out", "stderr": "err"}, True) == "out\nerr"
    assert render({"stdout": '{"a": 1}', "stderr": ""}, True) == '{\n  "a": 1\n}'
    reset = render({"stdout": "", "stderr": "warn\nShell cwd was reset to /p"}, True)
    assert reset == "warn\nShell cwd was reset to /p"
    assert render({"stdout": "", "stderr": "Shell cwd was reset to /p"}, True) == "Shell cwd was reset to /p"
    assert render({"stdout": "", "stderr": "  "}, True) == "(No output)"
    assert render({"stdout": "", "backgroundTaskId": "b1"}, True) == "Running in the background (↓ to manage)"
    assert render({"stdout": "", "noOutputExpected": True}, True) == "Done"
    assert render({"stdout": "", "returnCodeInterpretation": "No matches"}, True) == "No matches"
    assert render("x", True) is None
    # Python's integers never lose precision, so a big number formats (JS keeps the line as is).
    assert bash_ui.try_format_json('{"n": 12345678901234567890}') == '{\n  "n": 12345678901234567890\n}'
    assert bash_ui.try_format_json('{"a":1e2}') == '{"a":1e2}'  # the round trip changes it: kept
    assert bash_ui.try_format_json("not json") == "not json"
    assert bash_ui.try_format_json('{"u":"a\\/b"}') == '{\n  "u": "a/b"\n}'
    assert bash_ui.try_json_format_content("x" * 10_001) == "x" * 10_001


def test_mcp_results():
    render = mcp_ui.render_tool_result_message
    assert render([{"type": "text", "text": "a"}, {"type": "image"}, {"type": "other"}], True) == "a\n[Image]\n"
    assert render("", True) == "(No content)" and render(None, True) == "(No content)"
    assert render("plain", True) == "plain"
    big = "x" * 50_000
    warning = "⚠ Large MCP response (~12,500 tokens), this can fill up context quickly\n"
    assert str(render(big, True)).startswith(warning)


def test_structured_outputs_reach_the_tool_message():
    message = default_map_result(ToolResult(content="x", metadata={"a": 1}), "c1")
    assert message.additional_kwargs["tool_use_result"] == {"a": 1}
    assert "tool_use_result" not in default_map_result(ToolResult(content="x"), "c2").additional_kwargs


def test_the_tools_report_their_outputs(make_fake_workspace):
    ws = make_fake_workspace()
    ws.write_file("a.py", "x = 1\ny = 2\n")
    ctx = tool_context_for(AppStateStore(), workspace=ws)
    result = asyncio.run(build_read_file_tool().call({"file_path": "a.py"}, ctx))
    file = {"filePath": "a.py", "content": "x = 1\ny = 2", "numLines": 2, "startLine": 1, "totalLines": 2}
    assert result.metadata == {"type": "text", "file": file}


# ── the renderer ─────────────────────────────────────────────────────────────

def _renderer(tmp_path: Path, verbose: bool = False) -> CollapsedReadSearchRenderer:
    from app.screens.repl import render_message

    ctx = _ctx(tmp_path)
    return CollapsedReadSearchRenderer(lambda: TOOLS, lambda: ctx, render_message, verbose=verbose)


def _result(call_id: str, output: Any, status: str = "success") -> ToolMessage:
    return ToolMessage(content="raw", tool_call_id=call_id, status=status,
                       additional_kwargs={"tool_use_result": output})


def test_searches_collapse_into_one_summary(tmp_path):
    r = _renderer(tmp_path)
    lines = r.feed(AIMessage(content="", tool_calls=[_call("Grep", "1", pattern="def x"),
                                                     _call("ReadFile", "2", file_path="a.py")]))
    assert lines == ['  ⎿  "def x"', "  ⎿  a.py"]
    assert r.feed(_result("1", {"numFiles": 1})) == [] and r.feed(_result("2", {"type": "text"})) == []
    assert r.feed(HumanMessage(content="meta", additional_kwargs={"is_meta": True})) == []
    assert r.feed(AIMessage(content="", tool_calls=[_call("ReadFile", "3", file_path="a.py")])) == []
    lines = r.feed(AIMessage(content="Found it", tool_calls=[_call("Edit", "4", file_path="a.py")]))
    assert lines[0] == "  Searched for 1 pattern, read 1 file" and lines[1] == "● Found it"
    assert lines[2].startswith("⏺ Edit(")
    assert r.feed(_result("4", None)) == ["  ⎿ raw"]
    assert r.flush() == []


def test_a_foreign_result_or_user_message_closes_the_group(tmp_path):
    r = _renderer(tmp_path)
    r.feed(AIMessage(content="", tool_calls=[_call("Bash", "1", command="cat a")]))
    assert r.feed(ToolMessage(content="other", tool_call_id="zz")) == ["  Read 1 file", "  ⎿ other"]
    r.feed(AIMessage(content="", tool_calls=[_call("Glob", "2", pattern="*")]))
    assert r.feed(HumanMessage(content=[{"type": "text", "text": "[Request interrupted by user]"}])) == [
        "  Searched for 1 pattern"]
    r.feed(AIMessage(content="", tool_calls=[_call("Glob", "3", pattern="*")]))
    assert r.feed(SystemMessage(content="system")) == []
    assert r.feed(Terminated_like()) == ["  Searched for 1 pattern"]


class Terminated_like:
    pass


def test_a_multiline_hint_and_meta_assistant(tmp_path):
    r = _renderer(tmp_path)
    assert r.feed(AIMessage(content="", tool_calls=[_call("Bash", "1", command="cat a\ncat b")])) == [
        "  ⎿  $ cat a", "     cat b"]
    assert r.feed(AIMessage(content="x", additional_kwargs={"is_meta": True})) == []
    assert response_lines("one") == ["  ⎿  one"]


def test_relevant_memories_join_the_group(tmp_path):
    r = _renderer(tmp_path)
    r.feed(AIMessage(content="", tool_calls=[_call("ReadFile", "1", file_path="a.py")]))
    memories = HumanMessage(content="m", additional_kwargs={
        "is_meta": True,
        ATTACHMENT_KEY: {"type": "relevant_memories", "memories": [{"path": "/m/x.md", "content": "c"}]}})
    assert r.feed(memories) == []
    assert r.flush() == ["  Read 1 file, recalled 1 memory"]


def test_verbose_shows_each_call_and_its_result(tmp_path):
    r = _renderer(tmp_path, verbose=True)
    assert r.feed(AIMessage(content="", tool_calls=[_call("Grep", "1", pattern="x", path="src"),
                                                    _call("ReadFile", "2", file_path="a.py")])) == [
        '● Search(pattern: "x", path: "src")', "● Read(a.py)"]
    assert r.feed(_result("1", {"numFiles": 1, "filenames": ["src/a.py"]})) == ["  ⎿  Found 1 file", "     src/a.py"]
    assert r.feed(_result("2", {"type": "text"}, status="error")) == []
    r.group.absorb_relevant_memories([{"path": "/m/x.md", "content": "line1\nline2"}])  # type: ignore[union-attr]
    assert r.flush() == ["  ⎿  Recalled x.md", "     line1", "     line2"]
    r.feed(AIMessage(content="", tool_calls=[_call("Bash", "4", command="ls")]))
    assert r.feed(_result("4", "no mapping")) == []
    assert r._verbose_header(_call("Missing", "5"), TOOLS) == "● Missing"


def test_the_repl_and_cli_carry_verbose():
    from app.main import parse_args
    from app.session.driver import SessionOptions

    assert SessionOptions().verbose is False
    assert parse_args(["--verbose"]).verbose is True
