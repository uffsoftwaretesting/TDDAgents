"""Each tool's `user_facing_name` / `render_tool_use_message` (the ported `UI.tsx` functions)."""

from __future__ import annotations

from app.tool import ToolResult, build_tool, render_input_as_key_values
from app.tools.bash_tool import ui as bash_ui
from app.tools.file_edit_tool import ui as edit_ui
from app.tools.file_read_tool import ui as read_ui
from app.tools.file_write_tool import ui as write_ui
from app.tools.glob_tool import ui as glob_ui
from app.tools.grep_tool import ui as grep_ui
from app.tools.web_fetch_tool import ui as fetch_ui


def test_bash_shows_the_whole_command_when_verbose_and_truncates_otherwise():
    assert bash_ui.user_facing_name(None) == "Bash"
    assert bash_ui.render_tool_use_message({}, True) is None
    three = "a\nb\nc"
    assert bash_ui.render_tool_use_message({"command": three}, True) == three
    assert bash_ui.render_tool_use_message({"command": three}, False) == "a\nb…"
    assert bash_ui.render_tool_use_message({"command": "a\nb"}, False) == "a\nb"
    long = "x" * 159 + " y"
    assert bash_ui.render_tool_use_message({"command": long}, False) == "x" * 159 + "…"
    exact = "z" * 160
    assert bash_ui.render_tool_use_message({"command": exact}, False) == exact


def test_read_names_and_renders_the_path_and_range():
    assert read_ui.user_facing_name(None) == "Read"
    assert read_ui.render_tool_use_message({}, True) is None
    assert read_ui.render_tool_use_message({"file_path": "a.py"}, True) == "a.py"
    assert read_ui.render_tool_use_message({"file_path": "a.pdf", "pages": "1-3"}, False) == "a.pdf · pages 1-3"
    assert read_ui.render_tool_use_message({"file_path": "a.py", "offset": 5}, True) == "a.py · from line 5"
    assert read_ui.render_tool_use_message({"file_path": "a.py", "limit": 2}, True) == "a.py · lines 1-2"
    assert read_ui.render_tool_use_message({"file_path": "a.py", "offset": 5, "limit": 2}, False) == "a.py"


def test_edit_and_write_names():
    assert edit_ui.user_facing_name(None) == "Update"
    assert edit_ui.user_facing_name({"old_string": ""}) == "Create"
    assert edit_ui.user_facing_name({"old_string": "x"}) == "Update"
    assert edit_ui.render_tool_use_message({"file_path": "f"}, True) == "f"
    assert edit_ui.render_tool_use_message({}, True) is None
    assert write_ui.user_facing_name(None) == "Write"
    assert write_ui.render_tool_use_message({"file_path": "f"}, False) == "f"
    assert write_ui.render_tool_use_message({}, False) is None


def test_search_tools_render_pattern_and_path():
    for ui in (glob_ui, grep_ui):
        assert ui.user_facing_name(None) == "Search"
        assert ui.render_tool_use_message({}, True) is None
        assert ui.render_tool_use_message({"pattern": "p"}, True) == 'pattern: "p"'
        assert ui.render_tool_use_message({"pattern": "p", "path": "d"}, True) == 'pattern: "p", path: "d"'


def test_web_fetch_renders_the_url_and_the_prompt_when_verbose():
    assert fetch_ui.user_facing_name(None) == "Fetch"
    assert fetch_ui.render_tool_use_message({}, True) is None
    assert fetch_ui.render_tool_use_message({"url": "u", "prompt": "p"}, False) == "u"
    assert fetch_ui.render_tool_use_message({"url": "u", "prompt": "p"}, True) == 'url: "u", prompt: "p"'
    assert fetch_ui.render_tool_use_message({"url": "u"}, True) == 'url: "u"'


def test_build_tool_defaults_to_its_name_and_the_key_value_rendering():
    tool = build_tool(name="T", prompt="p", call=lambda a, c: ToolResult(content=""))
    assert tool.user_facing_name(None) == "T"
    assert tool.render_tool_use_message({"a": "é", "b": [1, 2]}, True) == 'a: "é", b: [1,2]'
    assert render_input_as_key_values({}) == ""
