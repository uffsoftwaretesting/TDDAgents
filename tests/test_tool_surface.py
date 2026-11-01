"""The Tool surface (Part 8a): widths, truncation, display paths, summaries, search/read classes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from app.bootstrap.state import set_cwd
from app.constants.tool_limits import (
    BYTES_PER_TOKEN,
    DEFAULT_MAX_RESULT_SIZE_CHARS,
    MAX_TOOL_RESULT_BYTES,
    MAX_TOOL_RESULT_TOKENS,
    MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
    TOOL_SUMMARY_MAX_LENGTH,
)
from app.ink.string_width import (
    east_asian_width,
    get_emoji_width,
    is_zero_width,
    needs_segmentation,
    string_width,
    strip_ansi,
)
from app.state.app_state_store import AppStateStore
from app.tool import (
    SearchOrRead,
    build_tool,
    get_empty_tool_permission_context,
    tool_context_for,
)
from app.tools.bash_tool.bash_tool import (
    BASH_LIST_COMMANDS,
    BASH_READ_COMMANDS,
    BASH_SEARCH_COMMANDS,
    BASH_SEMANTIC_NEUTRAL_COMMANDS,
    bash_search_or_read,
    build_bash_tool,
    is_search_or_read_bash_command,
)
from app.tools.file_edit_tool.file_edit_tool import build_edit_tool
from app.tools.file_read_tool import ui as read_ui
from app.tools.file_read_tool.file_read_tool import build_read_file_tool
from app.tools.file_write_tool.file_write_tool import build_write_file_tool
from app.tools.glob_tool.glob_tool import build_glob_tool
from app.tools.grep_tool.grep_tool import build_grep_tool
from app.tools.mcp_tool.classify_for_collapse import (
    READ_TOOLS,
    SEARCH_TOOLS,
    classify_mcp_tool_for_collapse,
    normalize,
)
from app.tools.web_fetch_tool.web_fetch_tool import build_web_fetch_tool
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.file import get_absolute_and_relative_paths, get_display_path
from app.utils.intl import first_grapheme, graphemes, last_grapheme
from app.utils.task.disk_output import get_task_output_dir
from app.utils.truncate import (
    truncate,
    truncate_path_middle,
    truncate_start_to_width,
    truncate_to_width,
    truncate_to_width_no_ellipsis,
    wrap_text,
)
from app.workspace.local import LocalWorkspace

FAMILY = "👨‍👩‍👧"  # one grapheme, a ZWJ sequence
FLAG = "🇧🇷"


# ── intl / stringWidth ───────────────────────────────────────────────────────

def test_graphemes_keep_clusters_whole():
    assert graphemes(f"a{FAMILY}é") == ["a", FAMILY, "é"]
    assert (first_grapheme(f"{FLAG}x"), last_grapheme(f"x{FAMILY}")) == (FLAG, FAMILY)
    assert (first_grapheme(""), last_grapheme(""), graphemes("")) == ("", "", [])


@pytest.mark.parametrize(("text", "width"), [
    ("", 0), ("abc", 3), ("a\tb\x01", 2), ("\x1b[31mred\x1b[0m", 3), ("\x1b[0m", 0),
    ("日本", 4), ("ｆ", 2), ("é", 1), ("é", 1), ("⚠", 1), ("­", 0), ("\u200b", 0),
    ("😀", 2), (FAMILY, 2), (FLAG, 2), ("🇧", 1), ("1️⃣", 2), ("#️", 1),
    ("☀️", 2), ("a😀b", 4), ("कि", 1), ("กั", 1),
])
def test_string_width_counts_terminal_columns(text, width):
    assert string_width(text) == width


def test_the_width_helpers():
    assert (east_asian_width(ord("中")), east_asian_width(ord("a")), east_asian_width(ord("α"))) == (2, 1, 1)
    assert needs_segmentation("x‍") and needs_segmentation("☀") and not needs_segmentation("abc é")
    assert (get_emoji_width("🇧"), get_emoji_width(FLAG), get_emoji_width("5️"), get_emoji_width("😀")) == (1, 2, 1, 2)
    assert strip_ansi("\x1b[1;32mok\x1b[0m \x1b]0;title\x07x") == "ok x"
    assert strip_ansi("\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\|") == "link|"
    assert string_width("\u26a0\ufe0f") == 2  # made emoji by the variation selector


@pytest.mark.parametrize("cp", [0x00, 0x1F, 0x7F, 0x9F, 0x00AD, 0x200B, 0x200D, 0xFEFF, 0x2060, 0x2064, 0xFE00,
                                0xFE0F, 0xE0100, 0x0300, 0x036F, 0x1AB0, 0x1DC0, 0x20D0, 0xFE20, 0x0900, 0x093A,
                                0x0951, 0x0962, 0x0E31, 0x0E34, 0x0E47, 0x0EB1, 0x0EB4, 0x0EC8, 0x0600, 0x06DD,
                                0x070F, 0x08E2, 0xD800, 0xE0000, 0xE007F])
def test_zero_width_code_points(cp):
    assert is_zero_width(cp)


@pytest.mark.parametrize("cp", [0x20, 0x41, 0x7E, 0xA0, 0xE9, 0x0904, 0x0E32, 0x0EB2, 0x0606, 0x4E2D, 0x200E])
def test_visible_code_points(cp):
    assert not is_zero_width(cp)


# ── truncate.ts ──────────────────────────────────────────────────────────────

def test_truncate_to_width_and_from_the_start():
    assert truncate_to_width("abcdef", 6) == "abcdef"
    assert truncate_to_width("abcdef", 4) == "abc…"
    assert truncate_to_width("abcdef", 1) == "…" and truncate_to_width("abcdef", 0) == "…"
    assert truncate_to_width("日本語", 4) == "日…"
    assert truncate_to_width(f"{FAMILY}{FAMILY}", 3) == f"{FAMILY}…"
    assert truncate_start_to_width("abcdef", 4) == "…def"
    assert truncate_start_to_width("abc", 3) == "abc" and truncate_start_to_width("abcd", 1) == "…"
    assert truncate_start_to_width("日本語", 4) == "…語"


def test_truncate_without_an_ellipsis():
    assert truncate_to_width_no_ellipsis("abcdef", 3) == "abc"
    assert truncate_to_width_no_ellipsis("abc", 3) == "abc"
    assert truncate_to_width_no_ellipsis("abcdef", 0) == ""
    assert truncate_to_width_no_ellipsis("日本語", 3) == "日"


def test_truncate_path_middle():
    path = "src/components/deeply/nested/folder/MyComponent.tsx"
    assert truncate_path_middle(path, 30) == "src/component…/MyComponent.tsx"
    assert truncate_path_middle("short.ts", 30) == "short.ts"
    assert truncate_path_middle(path, 0) == "…"
    assert truncate_path_middle(path, 4) == "src…"
    assert truncate_path_middle("a/VeryLongFileNameThatIsLong.tsx", 10) == "…sLong.tsx"
    assert truncate_path_middle("NoSlashButVeryLongName.ts", 10) == "…ngName.ts"
    assert truncate_path_middle("dir/ab.py", 8) == "d…/ab.py"
    assert truncate_path_middle("dir/ab.py", 9) == "dir/ab.py"


def test_truncate_single_line_and_wrap():
    assert truncate("short", 10) == "short"
    assert truncate("abcdefghijkl", 5) == "abcd…"
    assert truncate("first\nsecond", 20) == "first\nsecond"
    assert truncate("first\nsecond", 20, single_line=True) == "first…"
    assert truncate("firstline\nsecond", 6, single_line=True) == "first…"
    assert truncate("ab\ncd", 3, single_line=True) == "ab…"
    assert wrap_text("abcdef", 4) == ["abcd", "ef"]
    assert wrap_text("日本語", 4) == ["日本", "語"]
    assert wrap_text("", 4) == []


def test_tool_limits_are_upstreams():
    assert (DEFAULT_MAX_RESULT_SIZE_CHARS, MAX_TOOL_RESULT_TOKENS, BYTES_PER_TOKEN) == (50_000, 100_000, 4)
    assert (MAX_TOOL_RESULT_BYTES, MAX_TOOL_RESULTS_PER_MESSAGE_CHARS, TOOL_SUMMARY_MAX_LENGTH) == (
        400_000, 200_000, 50)


# ── getDisplayPath ───────────────────────────────────────────────────────────

def _ctx(root: Path) -> Any:
    return tool_context_for(AppStateStore(), workspace=LocalWorkspace(root))


def test_display_paths_are_relative_to_the_tracked_cwd(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    ctx = _ctx(root)
    assert get_display_path(str(root / "a.py"), ctx) == "a.py"
    assert get_display_path("sub/b.py", ctx) == "sub/b.py"
    assert get_display_path(str(root), ctx) == str(root)  # the cwd itself: no relative form
    assert get_display_path("/etc/hosts", ctx) == "/etc/hosts"
    set_cwd(ctx.workspace, str(root / "sub"))
    assert get_display_path("b.py", ctx) == "b.py"
    assert get_display_path(str(root / "sub" / "b.py"), ctx) == "b.py"
    assert get_display_path(str(root / "a.py"), ctx) == str(root / "a.py")


def test_display_paths_under_home_use_a_tilde(tmp_path):
    ctx = _ctx(tmp_path / "elsewhere")
    home = os.path.expanduser("~")
    assert get_display_path(f"{home}/notes.md", ctx) == "~/notes.md"
    assert get_display_path("~/notes.md", ctx) == "~/notes.md"


def test_absolute_and_relative_paths(tmp_path):
    ctx = _ctx(tmp_path)
    assert get_absolute_and_relative_paths(None, ctx) == (None, None)
    assert get_absolute_and_relative_paths("x/y", ctx) == (f"{tmp_path}/x/y", "x/y")
    assert get_absolute_and_relative_paths(str(tmp_path), ctx) == (str(tmp_path), "")


def test_without_a_context_the_process_cwd_is_used():
    assert get_display_path(f"{os.getcwd()}/app/x.py") == "app/x.py"


# ── each tool's getToolUseSummary / getActivityDescription ───────────────────

def test_file_tools_summarize_with_the_display_path(tmp_path):
    ctx = _ctx(tmp_path)
    for tool, verb, fallback in [(build_read_file_tool(), "Reading", "Reading file"),
                                 (build_edit_tool(), "Editing", "Editing file"),
                                 (build_write_file_tool(), "Writing", "Writing file")]:
        assert tool.get_tool_use_summary({"file_path": str(tmp_path / "a.py")}, ctx) == "a.py"
        assert tool.get_activity_description({"file_path": str(tmp_path / "a.py")}, ctx) == f"{verb} a.py"
        assert tool.get_tool_use_summary({}, ctx) is None and tool.get_tool_use_summary(None, ctx) is None
        assert tool.get_activity_description({"file_path": ""}, ctx) == fallback


def test_a_task_output_read_is_summarized_by_its_task_id(tmp_path):
    ctx = tool_context_for(AppStateStore(), workspace=LocalWorkspace(tmp_path), run_id="r1")
    task_dir = get_task_output_dir(ctx)
    assert read_ui.get_agent_output_task_id(f"{task_dir}/b12x_-9.output", ctx) == "b12x_-9"
    assert read_ui.get_tool_use_summary({"file_path": f"{task_dir}/b12x_-9.output"}, ctx) == "b12x_-9"
    for not_a_task in [f"{task_dir}/a.b.output", f"{task_dir}/{'x' * 21}.output", f"{task_dir}/.output",
                       f"{task_dir}/abc.txt", "/tmp/abc.output"]:
        assert read_ui.get_agent_output_task_id(not_a_task, ctx) is None
    assert read_ui.get_agent_output_task_id(f"{task_dir}/{'x' * 20}.output", ctx) == "x" * 20
    assert read_ui.get_agent_output_task_id(f"{task_dir}/abc.output") is None  # no context, no run


def test_search_and_fetch_tools_truncate_their_summary():
    long = "p" * 60
    cases = [(build_glob_tool(), "pattern", "Finding", "Finding files"),
             (build_grep_tool(), "pattern", "Searching for", "Searching"),
             (build_web_fetch_tool(), "url", "Fetching", "Fetching web page")]
    for tool, key, verb, fallback in cases:
        assert tool.get_tool_use_summary({key: "x*"}) == "x*"
        assert tool.get_tool_use_summary({key: long}) == "p" * 49 + "…"
        assert tool.get_activity_description({key: "x*"}) == f"{verb} x*"
        assert tool.get_activity_description({}) == fallback and tool.get_tool_use_summary(None) is None


def test_bash_summarizes_with_its_description_else_the_command():
    bash = build_bash_tool()
    long = "echo " + "x" * 60
    assert bash.get_tool_use_summary({"command": "ls", "description": "List files " * 10}) == "List files " * 10
    assert bash.get_tool_use_summary({"command": long}) == long[:49] + "…"
    assert bash.get_tool_use_summary({"command": "ls", "description": ""}) == "ls"
    assert bash.get_tool_use_summary({"command": ""}) is None and bash.get_tool_use_summary(None) is None
    assert bash.get_activity_description({"command": "ls", "description": "List"}) == "Running List"
    assert bash.get_activity_description({"command": "ls", "description": ""}) == "Running "
    assert bash.get_activity_description({"command": long}) == f"Running {long[:49]}…"
    assert bash.get_activity_description({}) == "Running command"


def test_a_tool_without_the_surface_answers_like_upstreams_absent_members():
    tool = build_tool(name="X", prompt="p", call=lambda a, c: None)  # type: ignore[arg-type, return-value]
    assert tool.get_tool_use_summary({"a": 1}) is None and tool.get_activity_description({"a": 1}) is None
    assert tool.is_search_or_read_command({}) is None
    assert tool.interrupt_behavior() == "block"
    cancel = build_tool(name="Y", prompt="p", call=lambda a, c: None,  # type: ignore[arg-type, return-value]
                        interrupt_behavior=lambda: "cancel")
    assert cancel.interrupt_behavior() == "cancel"


def test_the_empty_permission_context_is_upstreams():
    empty = get_empty_tool_permission_context()
    assert empty == ToolPermissionContext()
    assert (empty.mode, empty.always_allow_rules, empty.always_deny_rules, empty.always_ask_rules) == (
        PermissionMode.DEFAULT, (), (), ())
    assert (empty.additional_working_directories, empty.is_bypass_permissions_mode_available) == ((), False)
    assert AppStateStore().get().tool_permission_context == empty


# ── isSearchOrReadCommand ────────────────────────────────────────────────────

def test_the_file_and_search_tools_classify_themselves():
    assert build_read_file_tool().is_search_or_read_command({}) == SearchOrRead(False, True)
    assert build_grep_tool().is_search_or_read_command({}) == SearchOrRead(True, False)
    assert build_glob_tool().is_search_or_read_command({}) == SearchOrRead(True, False)
    for tool in (build_edit_tool(), build_write_file_tool(), build_web_fetch_tool()):
        assert tool.is_search_or_read_command({}) is None


def test_the_bash_command_sets_are_upstreams():
    assert BASH_SEARCH_COMMANDS == {"find", "grep", "rg", "ag", "ack", "locate", "which", "whereis"}
    assert BASH_READ_COMMANDS == {"cat", "head", "tail", "less", "more", "wc", "stat", "file", "strings", "jq",
                                  "awk", "cut", "sort", "uniq", "tr"}
    assert BASH_LIST_COMMANDS == {"ls", "tree", "du"}
    assert BASH_SEMANTIC_NEUTRAL_COMMANDS == {"echo", "printf", "true", "false", ":"}


@pytest.mark.parametrize(("command", "expected"), [
    ("grep -r x .", (True, False, False)),
    ("cat a | sort | uniq", (False, True, False)),
    ("grep x f | wc -l", (True, True, False)),
    ("ls dir && echo --- && ls dir2", (False, False, True)),
    ("find . -name '*.py' > out.txt", (True, False, False)),
    ("cat a >> log", (False, True, False)),
    ("cat a 2>&1", (False, True, False)),
    ("tree; du -sh .", (False, False, True)),
    ("cat a || true", (False, True, False)),
    ("echo hi", (False, False, False)),
    ("echo a; printf b", (False, False, False)),
    ("cat a && rm a", (False, False, False)),
    ("npm test", (False, False, False)),
    ("", (False, False, False)),
])
def test_bash_search_or_read(command, expected):
    assert is_search_or_read_bash_command(command) == SearchOrRead(*expected)


def test_bash_classification_survives_bad_input(monkeypatch):
    assert bash_search_or_read({"command": 3}) == SearchOrRead(False, False, False)
    assert bash_search_or_read({}) == SearchOrRead(False, False, False)
    assert build_bash_tool().is_search_or_read_command({"command": "rg x"}) == SearchOrRead(True, False, False)

    def boom(command: str) -> list[str]:
        raise ValueError("unparseable")

    monkeypatch.setattr("app.tools.bash_tool.bash_tool.split_command_with_operators", boom)
    assert is_search_or_read_bash_command("cat a") == SearchOrRead(False, False, False)
    monkeypatch.setattr("app.tools.bash_tool.bash_tool.split_command_with_operators", lambda c: [])
    assert is_search_or_read_bash_command("cat a") == SearchOrRead(False, False, False)
    monkeypatch.setattr("app.tools.bash_tool.bash_tool.split_command_with_operators", lambda c: ["  ", "cat a"])
    assert is_search_or_read_bash_command("cat a") == SearchOrRead(False, True, False)


def test_mcp_tools_collapse_by_their_normalized_name():
    assert normalize("searchMessages") == "search_messages" and normalize("get-FooBar") == "get_foo_bar"
    assert classify_mcp_tool_for_collapse("any", "slack_search_public") == SearchOrRead(True, False)
    assert classify_mcp_tool_for_collapse("any", "slack-search-public") == SearchOrRead(True, False)
    assert classify_mcp_tool_for_collapse("srv", "send_message") == SearchOrRead(False, False)
    some_read = min(READ_TOOLS)
    assert classify_mcp_tool_for_collapse("srv", some_read).is_read
    assert (len(SEARCH_TOOLS), len(READ_TOOLS)) == (87, 402)


def test_the_mcp_lists_are_upstreams_verbatim():
    import re

    source = Path("reference/claude-code/src/tools/MCPTool/classifyForCollapse.ts")
    if not source.exists():
        pytest.skip("reference snapshot absent")
    text = source.read_text()
    for name, ours in (("SEARCH_TOOLS", SEARCH_TOOLS), ("READ_TOOLS", READ_TOOLS)):
        match = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", text, re.DOTALL)
        assert match is not None
        body = re.sub(r"//[^\n]*", "", match.group(1))
        assert set(re.findall(r"'([^']+)'", body)) == ours
