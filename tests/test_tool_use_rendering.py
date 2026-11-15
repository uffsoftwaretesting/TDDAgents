"""
How each tool's call and result read in the REPL: `user_facing_name`, `render_tool_use_message` and
`render_tool_result_message`, as the ported `UI.tsx` functions write them, and the `context` the
renderers take in place of upstream's process-wide cwd.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from app.components.file_edit_tool_updated_message import (
    file_edit_tool_updated_message,
    updated_summary,
)
from app.components.structured_diff.fallback import (
    format_diff,
    number_diff_lines,
    transform_lines_to_objects,
)
from app.components.structured_diff_list import HUNK_SEPARATOR, structured_diff_list
from app.skills.load_skills_dir import SkillRegistry
from app.state.app_state_store import AppStateStore
from app.tdd.run_tests_tool.run_tests_tool import build_run_tests_tool
from app.tdd.tdd_plan_tool import ui as plan_ui
from app.tdd.tdd_plan_tool.tdd_plan_tool import build_update_plan_tool
from app.tool import (
    ToolResult,
    build_tool,
    default_map_result,
    takes_context,
    tool_context_for,
)
from app.tools.agent_tool import ui as agent_ui
from app.tools.agent_tool.agent_tool import build_agent_tool
from app.tools.ask_user_question_tool.ask_user_question_tool import (
    build_ask_user_question_tool,
)
from app.tools.enter_plan_mode_tool.enter_plan_mode_tool import (
    build_enter_plan_mode_tool,
)
from app.tools.exit_plan_mode_tool.exit_plan_mode_tool import build_exit_plan_mode_tool
from app.tools.file_edit_tool import ui as edit_ui
from app.tools.file_edit_tool.file_edit_tool import build_edit_tool
from app.tools.file_read_tool import ui as read_ui
from app.tools.file_read_tool.file_read_tool import build_read_file_tool
from app.tools.file_write_tool import ui as write_ui
from app.tools.file_write_tool.file_write_tool import build_write_file_tool
from app.tools.glob_tool.glob_tool import build_glob_tool
from app.tools.grep_tool.grep_tool import build_grep_tool
from app.tools.list_mcp_resources_tool.list_mcp_resources_tool import (
    build_list_mcp_resources_tool,
)
from app.tools.read_mcp_resource_tool.read_mcp_resource_tool import (
    build_read_mcp_resource_tool,
)
from app.tools.send_message_tool.send_message_tool import build_send_message_tool
from app.tools.skill_tool.skill_tool import build_skill_tool
from app.tools.task_output_tool.task_output_tool import build_task_output_tool
from app.tools.task_stop_tool.task_stop_tool import build_task_stop_tool
from app.tools.todo_write_tool.todo_write_tool import build_todo_write_tool
from app.utils.diff import (
    FileEdit,
    format_hunk,
    get_patch_from_contents,
    get_structured_patch_for_display,
    get_structured_patch_from_contents,
)
from app.utils.task.disk_output import get_task_output_dir
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace
from tests.test_loop_engine import CONFIG, FakeModel, make_fake_deps


def _ctx(root: Path) -> Any:
    return tool_context_for(AppStateStore(), workspace=LocalWorkspace(root), run_id="r1")


def _row(tool: Any, args: dict[str, Any], verbose: bool = False, context: Any = None) -> str | None:
    """The call's row as `AssistantToolUseMessage` builds it, for a tool whose rendering is not `None`."""
    rendered = tool.render_tool_use_message(args, verbose, context)
    if rendered is None:
        return None
    return f"{tool.user_facing_name(args)}({rendered})" if rendered else tool.user_facing_name(args)


# ── the renderers' context ───────────────────────────────────────────────────

def test_a_two_argument_renderer_is_called_without_the_context():
    seen: list[tuple[Any, ...]] = []

    def two(input_args: dict[str, Any], verbose: bool) -> str:
        seen.append((input_args, verbose))
        return "two"

    tool = build_tool(name="T", prompt="p", call=lambda a, c: ToolResult(content=""),
                      render_tool_use_message=two, render_tool_result_message=lambda out, verbose: f"{out}-{verbose}")
    assert tool.render_tool_use_message({"a": 1}, True) == "two"
    assert tool.render_tool_use_message({"a": 2}, False, "ctx") == "two"
    assert seen == [({"a": 1}, True), ({"a": 2}, False)]
    assert tool.render_tool_result_message("out", True) == "out-True"
    assert tool.render_tool_result_message("out", False, "ctx") == "out-False"


def test_a_three_argument_renderer_gets_the_context():
    tool = build_tool(name="T", prompt="p", call=lambda a, c: ToolResult(content=""),
                      render_tool_use_message=lambda args, verbose, context: f"{args['a']}|{verbose}|{context}",
                      render_tool_result_message=lambda out, verbose, context: f"{out}|{verbose}|{context}")
    assert tool.render_tool_use_message({"a": 1}, True, "ctx") == "1|True|ctx"
    assert tool.render_tool_use_message({"a": 1}, False) == "1|False|None"
    assert tool.render_tool_result_message("o", True, "ctx") == "o|True|ctx"
    assert tool.render_tool_result_message("o", False) == "o|False|None"


def test_no_result_renderer_renders_nothing_and_no_use_renderer_is_the_key_value_join():
    tool = build_tool(name="T", prompt="p", call=lambda a, c: ToolResult(content=""))
    assert tool.render_tool_result_message({"x": 1}, True, "ctx") is None
    assert tool.render_tool_use_message({"a": "b"}, False, "ctx") == 'a: "b"'


def test_takes_context_counts_positional_parameters():
    def keyword_only(a: Any, b: Any, *, context: Any = None) -> None:
        return None

    def three(a: Any, b: Any, c: Any = None) -> None:
        return None

    def positional_only(a: Any, b: Any, c: Any, /) -> None:
        return None

    def variadic(*args: Any) -> None:
        return None

    assert takes_context(keyword_only) is False
    assert takes_context(three) is True
    assert takes_context(positional_only) is True
    assert takes_context(variadic) is True
    assert takes_context(lambda a, b: None) is False
    assert takes_context(max) is False  # a builtin without a signature


# ── display paths (`verbose ? path : getDisplayPath(path)`) ──────────────────

def test_the_path_tools_show_the_display_path_unless_verbose(tmp_path):
    ctx = _ctx(tmp_path)
    inside, outside = str(tmp_path / "src" / "a.py"), "/etc/hosts"
    for tool in (build_write_file_tool(), build_edit_tool(), build_read_file_tool()):
        assert tool.render_tool_use_message({"file_path": inside}, False, ctx) == "src/a.py"
        assert tool.render_tool_use_message({"file_path": inside}, True, ctx) == inside
        assert tool.render_tool_use_message({"file_path": outside}, False, ctx) == outside
        assert tool.render_tool_use_message({}, False, ctx) is None
    read = build_read_file_tool()
    assert read.render_tool_use_message({"file_path": inside, "pages": "1-2"}, False, ctx) == "src/a.py · pages 1-2"
    assert read.render_tool_use_message({"file_path": inside, "offset": 3}, True, ctx) == f"{inside} · from line 3"


def test_the_search_tools_show_the_display_path_of_their_path_unless_verbose(tmp_path):
    ctx = _ctx(tmp_path)
    inside = str(tmp_path / "src")
    for tool in (build_glob_tool(), build_grep_tool()):
        assert tool.render_tool_use_message({"pattern": "*.py", "path": inside}, False, ctx) == (
            'pattern: "*.py", path: "src"')
        assert tool.render_tool_use_message({"pattern": "*.py", "path": inside}, True, ctx) == (
            f'pattern: "*.py", path: "{inside}"')
        assert tool.render_tool_use_message({"pattern": "*.py"}, False, ctx) == 'pattern: "*.py"'


def test_a_read_of_a_task_output_file_shows_the_name_alone(tmp_path):
    ctx = _ctx(tmp_path)
    output = f"{get_task_output_dir(ctx)}/a1b2.output"
    tool = build_read_file_tool()
    assert tool.render_tool_use_message({"file_path": output}, False, ctx) == ""
    assert tool.render_tool_use_message({"file_path": output}, True, ctx) == ""
    assert _row(tool, {"file_path": output}, context=ctx) == "Read"
    # without a run there is no task directory, so it is an ordinary path
    assert read_ui.render_tool_use_message({"file_path": output}, True) == output


# ── the ported headers ───────────────────────────────────────────────────────

def test_agent_names_its_type_and_shows_its_description():
    assert agent_ui.user_facing_name(None) == "Agent"
    assert agent_ui.user_facing_name({}) == "Agent"
    assert agent_ui.user_facing_name({"subagent_type": "tester"}) == "tester"
    assert agent_ui.user_facing_name({"subagent_type": "worker"}) == "Agent"
    assert agent_ui.user_facing_name({"subagent_type": "general-purpose"}) == "Agent"
    assert agent_ui.render_tool_use_message({"description": "Write tests", "prompt": "p"}, False) == "Write tests"
    assert agent_ui.render_tool_use_message({"description": "Write tests"}, False) is None
    assert agent_ui.render_tool_use_message({"prompt": "p"}, False) is None
    tool = build_agent_tool(definitions={}, worker_tools=(), config=CONFIG,
                            deps=make_fake_deps(FakeModel([[AIMessage(content="r")]])))
    assert _row(tool, {"description": "Write tests", "prompt": "p", "subagent_type": "tester"}) == "tester(Write tests)"


def test_tools_with_no_row_render_none_and_no_name():
    for tool in (build_ask_user_question_tool(), build_enter_plan_mode_tool(), build_exit_plan_mode_tool(),
                 build_todo_write_tool()):
        assert tool.user_facing_name({}) == ""
        assert tool.render_tool_use_message({"plan": "x", "todos": [], "questions": []}, True) is None


def test_mcp_resource_tools():
    listing, reading = build_list_mcp_resources_tool(), build_read_mcp_resource_tool()
    assert listing.user_facing_name(None) == "listMcpResources"
    assert listing.render_tool_use_message({"server": "docs"}, False) == 'List MCP resources from server "docs"'
    assert listing.render_tool_use_message({}, False) == "List all MCP resources"
    assert reading.user_facing_name(None) == "readMcpResource"
    assert _row(reading, {"server": "docs", "uri": "file:///a"}) == (
        'readMcpResource(Read resource "file:///a" from server "docs")')
    assert reading.render_tool_use_message({"server": "docs"}, False) is None
    assert reading.render_tool_use_message({"uri": "file:///a"}, False) is None


def test_send_message_has_no_row_for_a_plain_message():
    tool = build_send_message_tool(definitions={}, worker_tools=(), config=CONFIG,
                                   deps=make_fake_deps(FakeModel([[AIMessage(content="r")]])))
    assert tool.user_facing_name(None) == "SendMessage"
    assert tool.render_tool_use_message({"to": "a1", "message": "hi"}, True) is None


def test_skill_shows_the_skill_name():
    tool = build_skill_tool(SkillRegistry(()), config=CONFIG, deps=make_fake_deps(FakeModel([])))
    assert _row(tool, {"skill": "tdd-project-config", "args": "python"}) == "Skill(tdd-project-config)"
    assert tool.render_tool_use_message({}, False) is None


def test_task_tools():
    output, stop = build_task_output_tool(), build_task_stop_tool()
    assert _row(output, {"task_id": "a1"}) == "Task Output"
    assert _row(output, {"task_id": "a1", "block": True}) == "Task Output"
    assert _row(output, {"task_id": "a1", "block": False}) == "Task Output(non-blocking)"
    assert _row(stop, {"task_id": "a1"}) == "Stop Task"


# ── TDDAgents' own tools ─────────────────────────────────────────────────────

def test_run_tests_reads_as_its_name_alone():
    tool = build_run_tests_tool()
    assert tool.render_tool_use_message({}, False) == ""
    assert _row(tool, {}) == "RunTests"


def test_update_plan_summarizes_each_operation():
    tool = build_update_plan_tool()
    operations = [{"op": "add", "text": "Parse the input", "position": 2},
                  {"op": "edit", "id": "2", "text": "x" * 60},
                  {"op": "remove", "id": "3"},
                  {"op": "move", "id": "4", "position": 1},
                  {"op": "edit", "id": "5", "text": "first line\nsecond line"}]
    assert tool.render_tool_use_message({"operations": operations, "reason": "r"}, False) == (
        f'add "Parse the input", edit 2 "{"x" * 49}…", remove 3, move 4, edit 5 "first line…"')
    assert _row(tool, {"operations": [{"op": "remove", "id": "1"}]}) == "UpdatePlan(remove 1)"
    assert plan_ui.render_tool_use_message({"operations": []}, False) is None
    assert plan_ui.render_tool_use_message({}, False) is None
    assert plan_ui.render_tool_use_message({"operations": ["add"]}, False) is None


# ── the structured patch ─────────────────────────────────────────────────────

def test_structured_hunks_carry_upstreams_fields():
    assert get_structured_patch_from_contents("a\nb\nc\n", "a\nB\nc\n") == [
        {"oldStart": 1, "oldLines": 3, "newStart": 1, "newLines": 3, "lines": [" a", "-b", "+B", " c"]}]
    assert get_structured_patch_from_contents("same", "same") == []
    assert get_structured_patch_for_display("\tx\ny", [FileEdit("y", "z")]) == [
        {"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2,
         "lines": ["   x", "-y", "\\ No newline at end of file", "+z", "\\ No newline at end of file"]}]
    # Tabs are converted for display only: `getPatchFromContents` keeps them, as upstream.
    assert get_structured_patch_from_contents("\tx\n", "\ty\n")[0]["lines"] == ["-\tx", "+\ty"]
    assert get_structured_patch_for_display("y y\n", [FileEdit("y", "z", replace_all=True)])[0]["lines"] == [
        "-y y", "+z z"]


def test_a_hunk_header_is_difflibs():
    assert format_hunk({"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 2, "lines": ["-a", "+b", "+c"]}) == (
        "@@ -1 +1,2 @@\n-a\n+b\n+c")
    assert get_patch_from_contents("a\nb\nc", "a\nB\nc") == [
        "@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n\\ No newline at end of file"]
    assert get_patch_from_contents("", "x\n") == ["@@ -0,0 +1 @@\n+x"]  # an empty side: `start-1,0`
    assert get_patch_from_contents("x\n", "") == ["@@ -1 +0,0 @@\n-x"]


def _far_apart_change() -> list[dict[str, Any]]:
    old = "\n".join(f"l{i}" for i in range(1, 21))
    new = old.replace("l2\n", "L2\n").replace("l18\n", "")
    return get_structured_patch_from_contents(old, new)


def test_diff_lines_are_numbered_from_the_hunks_old_start():
    lines = [" a", "-b", "-c", "+B", " d", "+e"]
    assert transform_lines_to_objects(lines)[:2] == [("a", "nochange"), ("b", "remove")]
    assert [(line.code, line.i) for line in number_diff_lines(transform_lines_to_objects(lines), 8)] == [
        ("a", 8), ("b", 9), ("c", 10), ("B", 9), ("d", 10), ("e", 11)]
    assert format_diff(lines, 8) == ["  8  a", "  9 -b", " 10 -c", "  9 +B", " 10  d", " 11 +e"]
    assert format_diff([], 1) == []


def test_hunks_are_separated_by_an_ellipsis_line():
    hunks = _far_apart_change()
    assert len(hunks) == 2
    assert structured_diff_list(hunks) == [
        " 1  l1", " 2 -l2", " 2 +L2", " 3  l3", " 4  l4", " 5  l5",
        HUNK_SEPARATOR,
        " 15  l15", " 16  l16", " 17  l17", " 18 -l18", " 18  l19", " 19  l20",
        " 20   No newline at end of file"]  # the marker reads as a kept line, as upstream's Fallback numbers it
    assert HUNK_SEPARATOR == "..."


def test_the_updated_summary_counts_added_and_removed_lines():
    def hunk(*lines: str) -> list[dict[str, Any]]:
        return [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1, "lines": list(lines)}]

    assert updated_summary(hunk("+a")) == "Added 1 line"
    assert updated_summary(hunk("+a", "+b")) == "Added 2 lines"
    assert updated_summary(hunk("-a")) == "Removed 1 line"
    assert updated_summary(hunk("-a", "-b", " c")) == "Removed 2 lines"
    assert updated_summary(hunk("-a", "+b")) == "Added 1 line, removed 1 line"
    assert updated_summary(hunk("-a", "-b", "+c", "+d")) == "Added 2 lines, removed 2 lines"
    assert updated_summary(hunk(" a")) == ""
    assert updated_summary(_far_apart_change()) == "Added 1 line, removed 2 lines"
    assert file_edit_tool_updated_message(hunk(" a", "-b", "+c")) == "Added 1 line, removed 1 line"  # condensed
    assert file_edit_tool_updated_message(hunk(" a", "-b", "+c"), True) == (
        "Added 1 line, removed 1 line\n 1  a\n 2 -b\n 2 +c")


# ── Write / Edit results ─────────────────────────────────────────────────────

def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_write_returns_upstreams_create_output_and_renders_the_written_lines(tmp_path):
    ctx = _ctx(tmp_path)
    path = str(tmp_path / "pkg" / "a.txt")
    tool = build_write_file_tool()
    result = _run(tool.call({"file_path": path, "content": "a\nb\n"}, ctx))
    assert result.content == f"File created successfully at: {path}"
    assert result.metadata == {"type": "create", "filePath": path, "content": "a\nb\n", "structuredPatch": [],
                               "originalFile": None}
    assert default_map_result(result, "t1").additional_kwargs["tool_use_result"] == result.metadata
    assert tool.render_tool_result_message(result.metadata, False, ctx) == "Wrote 2 lines to pkg/a.txt"
    assert tool.render_tool_result_message(result.metadata, True, ctx) == f"Wrote 2 lines to {path}\na\nb\n"


def test_a_long_created_file_shows_its_first_lines_and_how_many_are_left(tmp_path):
    ctx = _ctx(tmp_path)
    path = str(tmp_path / "a.txt")
    twelve = "\n".join(f"l{i}" for i in range(1, 13))
    output = {"type": "create", "filePath": path, "content": twelve}
    first_ten = "\n".join(f"l{i}" for i in range(1, 11))
    assert write_ui.render_tool_result_message(output, False, ctx) == "Wrote 12 lines to a.txt"  # condensed
    assert write_ui.render_tool_result_message(output, True, ctx) == f"Wrote 12 lines to {path}\n{twelve}"
    eleven = {**output, "content": twelve.rsplit("\n", 1)[0]}
    assert write_ui.render_tool_result_message(eleven, False, ctx) == "Wrote 11 lines to a.txt"
    ten = {**output, "content": first_ten + "\n"}
    assert write_ui.render_tool_result_message(ten, True, ctx) == f"Wrote 10 lines to {path}\n{first_ten}\n"


def test_an_empty_created_file_reads_no_content():
    assert write_ui.render_tool_result_message({"type": "create", "filePath": "", "content": ""}, True) == (
        "Wrote 1 lines to \n(No content)")
    assert write_ui.render_tool_result_message({"type": "create", "filePath": "", "content": ""}, False) == (
        "Wrote 1 lines to ")
    assert write_ui.count_lines("") == 1
    assert write_ui.count_lines("a") == 1
    assert write_ui.count_lines("a\n") == 1
    assert write_ui.count_lines("a\n\n") == 2


def test_write_over_an_existing_file_returns_the_update_and_its_hunks():
    ws = FakeWorkspace(files={"a.txt": "a\nb\nc\n"})
    ctx = tool_context_for(AppStateStore(), workspace=ws)
    _run(build_read_file_tool().call({"file_path": "a.txt"}, ctx))
    tool = build_write_file_tool()
    result = _run(tool.call({"file_path": "a.txt", "content": "a\nB\nc\n"}, ctx))
    assert result.content == "The file a.txt has been updated successfully."
    patch = [{"oldStart": 1, "oldLines": 3, "newStart": 1, "newLines": 3, "lines": [" a", "-b", "+B", " c"]}]
    assert result.metadata == {"type": "update", "filePath": "a.txt", "content": "a\nB\nc\n",
                               "structuredPatch": patch, "originalFile": "a\nb\nc\n"}
    expected = "Added 1 line, removed 1 line\n 1  a\n 2 -b\n 2 +B\n 3  c"
    assert tool.render_tool_result_message(result.metadata, False) == "Added 1 line, removed 1 line"
    assert tool.render_tool_result_message(result.metadata, True) == expected


def test_write_over_an_empty_file_is_a_create_as_upstreams_if_old_content():
    ws = FakeWorkspace(files={"a.txt": ""})
    ctx = tool_context_for(AppStateStore(), workspace=ws)
    _run(build_read_file_tool().call({"file_path": "a.txt"}, ctx))
    result = _run(build_write_file_tool().call({"file_path": "a.txt", "content": "x"}, ctx))
    assert result.content == "File created successfully at: a.txt"
    assert result.metadata is not None and result.metadata["type"] == "create"


def test_write_renders_nothing_for_an_unknown_output():
    assert write_ui.render_tool_result_message("text", False) is None
    assert write_ui.render_tool_result_message({"type": "other"}, False) is None


def test_edit_returns_upstreams_output_and_renders_its_hunks():
    ws = FakeWorkspace(files={"a.txt": "a\nb\nc\nb\n"})
    ctx = tool_context_for(AppStateStore(), workspace=ws)
    _run(build_read_file_tool().call({"file_path": "a.txt"}, ctx))
    tool = build_edit_tool()
    result = _run(tool.call({"file_path": "a.txt", "old_string": "b", "new_string": "B", "replace_all": True}, ctx))
    assert result.content == "The file a.txt has been updated. All occurrences were successfully replaced."
    assert result.metadata == {
        "filePath": "a.txt", "oldString": "b", "newString": "B", "originalFile": "a\nb\nc\nb\n",
        "structuredPatch": [{"oldStart": 1, "oldLines": 4, "newStart": 1, "newLines": 4,
                             "lines": [" a", "-b", "+B", " c", "-b", "+B"]}],
        "userModified": False, "replaceAll": True}
    assert tool.render_tool_result_message(result.metadata, False) == "Added 2 lines, removed 2 lines"
    assert tool.render_tool_result_message(result.metadata, True) == (
        "Added 2 lines, removed 2 lines\n 1  a\n 2 -b\n 2 +B\n 3  c\n 4 -b\n 4 +B")
    assert edit_ui.render_tool_result_message(None, True) is None


def test_edit_creating_a_file_has_an_empty_original():
    ws = FakeWorkspace()
    ctx = tool_context_for(AppStateStore(), workspace=ws)
    result = _run(build_edit_tool().call({"file_path": "new.txt", "old_string": "", "new_string": "x"}, ctx))
    assert result.content == "The file new.txt has been updated successfully."
    assert result.metadata == {
        "filePath": "new.txt", "oldString": "", "newString": "x", "originalFile": "",
        "structuredPatch": [{"oldStart": 1, "oldLines": 0, "newStart": 1, "newLines": 1,
                             "lines": ["+x", "\\ No newline at end of file"]}],
        "userModified": False, "replaceAll": False}
    assert edit_ui.render_tool_result_message(result.metadata, True) == (
        "Added 1 line\n 1 +x\n 2   No newline at end of file")
    assert edit_ui.render_tool_result_message(result.metadata, False) == "Added 1 line"  # no phantom empty line


# ── RunTests' result line (TDDAgents' own, 2026-11-14) ──────────────────────

def test_run_tests_output_and_its_verdict_line():
    from app.tdd.coverage_gate import CoverageReading, CoverageStatus
    from app.tdd.junit_report import JUnitReport
    from app.tdd.ledger import TddPhase
    from app.tdd.run_tests_tool import ui as run_ui
    from app.tdd.run_tests_tool.run_tests_tool import SuiteExecutionResult, run_tests_output

    failing = SuiteExecutionResult(exit_code=1, report=JUnitReport(tests=12, failures=2),
                                   coverage=CoverageReading(CoverageStatus.MEASURED, 84.0))
    output = run_tests_output(failing, TddPhase.RED)
    assert output == {"exitCode": 1, "tests": 12, "failures": 2, "errors": 0, "skipped": 0, "coverage": 84.0,
                      "phase": "RED"}
    assert run_ui.render_tool_result_message(output, False) == "exit 1 · 12 tests, 2 failed · coverage 84.0% · RED"
    passing = SuiteExecutionResult(exit_code=0, report=JUnitReport(tests=1),
                                   coverage=CoverageReading(CoverageStatus.MEASURED, 100.0))
    assert run_ui.render_tool_result_message(run_tests_output(passing, TddPhase.GREEN), True) == (
        "exit 0 · 1 test passed · coverage 100.0% · GREEN")
    mixed = SuiteExecutionResult(exit_code=1, report=JUnitReport(tests=5, errors=1, skipped=2))
    assert run_ui.render_tool_result_message(run_tests_output(mixed, None), False) == (
        "exit 1 · 5 tests, 1 errored, 2 skipped")  # no coverage measured, no current item
    no_report = run_tests_output(SuiteExecutionResult(exit_code=0), None)
    assert no_report["tests"] is None and no_report["coverage"] is None
    assert run_ui.render_tool_result_message(no_report, False) == "exit 0 · no JUnit report"
    empty = SuiteExecutionResult(exit_code=0, report=JUnitReport(tests=0))
    assert run_ui.render_tool_result_message(run_tests_output(empty, None), False) == "exit 0 · 0 tests"
    assert run_ui.render_tool_result_message({"tests": 1}, False) is None
    assert run_ui.render_tool_result_message(None, False) is None
