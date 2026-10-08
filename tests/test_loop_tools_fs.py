"""
The loop's file primitives, ported from claude-code's FileRead/FileWrite/FileEdit/Glob/Grep
tools, against a real LocalWorkspace and a root-less FakeWorkspace.

The FakeWorkspace cases matter: the sandbox workspace has no host `root`, so a tool that
reached past the `Workspace` protocol would work locally and crash in production. Paths
are workspace-relative throughout — an absolute host path is re-rooted under the workspace
by `normalize_path`, so a test using one passes for the wrong reason.
"""

import asyncio
import os
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage

from app.loop.context import AppStateStore, FileState, tool_context_for
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.tdd import check_tdd_phase_permission
from app.loop.tools import fs
from app.loop.tools.execution import run_tool_use
from app.loop.tools.fs import (
    DEFAULT_HEAD_LIMIT,
    FILE_MODIFIED_SINCE_READ_ERROR,
    FILE_NOT_FOUND_CWD_NOTE,
    FILE_NOT_READ_ERROR,
    FILE_UNEXPECTEDLY_MODIFIED_ERROR,
    GLOB_MAX_RESULTS,
    MAX_COLUMNS,
    MAX_LINES_TO_READ,
    VCS_DIRECTORIES_TO_EXCLUDE,
    add_line_numbers,
    apply_edit_to_file,
    apply_head_limit,
    build_edit_tool,
    build_glob_tool,
    build_grep_tool,
    build_read_file_tool,
    build_write_file_tool,
    find_actual_string,
    format_limit_info,
    get_prompt,
    normalize_quotes,
    path_kind,
    preserve_quote_style,
    split_grep_globs,
)
from app.loop.tools.pool import assemble_tool_pool
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace

L1, R1, L2, R2 = "‘", "’", "“", "”"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def local_ws(tmp_path):
    return LocalWorkspace(str(tmp_path))


def ctx_for(ws=None):
    return tool_context_for(AppStateStore(), workspace=ws)


def read(ctx, path, **kw):
    return run(build_read_file_tool().call({"file_path": path, **kw}, ctx))


def validate(builder, args, ctx):
    return builder().validate_input(args, ctx)


def via_pipeline(builder, args, ctx):
    tool = builder()
    call = cast(Any, {"id": "c1", "name": tool.name, "args": args})
    outcome = run(run_tool_use(call, AIMessage(content="", tool_calls=[call]), ctx, [tool]))
    return outcome.message


ALL_BUILDERS = [build_read_file_tool, build_write_file_tool, build_edit_tool, build_glob_tool, build_grep_tool]


# ── constants are the upstream values ────────────────────────────────────────

def test_upstream_constants():
    assert MAX_LINES_TO_READ == 2000
    assert GLOB_MAX_RESULTS == 100
    assert DEFAULT_HEAD_LIMIT == 250
    assert MAX_COLUMNS == 500
    assert VCS_DIRECTORIES_TO_EXCLUDE == (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")
    assert FILE_NOT_READ_ERROR == "File has not been read yet. Read it first before writing to it."
    assert FILE_MODIFIED_SINCE_READ_ERROR == (
        "File has been modified since read, either by the user or by a linter. "
        "Read it again before attempting to write it.")
    assert FILE_UNEXPECTEDLY_MODIFIED_ERROR == (
        "File has been unexpectedly modified. Read it again before attempting to write it.")


# ── grep helpers ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, expected", [
    ("*.py", ["*.py"]),
    ("*.py *.txt", ["*.py", "*.txt"]),
    ("*.py,*.txt", ["*.py", "*.txt"]),
    ("*.{ts,tsx}", ["*.{ts,tsx}"]),
    ("*.{ts,tsx} *.md,*.rst", ["*.{ts,tsx}", "*.md", "*.rst"]),
    ("a,,b", ["a", "b"]),
    ("", []),
])
def test_split_grep_globs(raw, expected):
    assert split_grep_globs(raw) == expected


def test_path_kind(local_ws, tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.txt").write_text("x")
    assert path_kind(local_ws, ".") == "dir"
    assert path_kind(local_ws, "") == "dir"
    assert path_kind(local_ws, "d") == "dir"
    assert path_kind(local_ws, "d/f.txt") == "file"
    assert path_kind(local_ws, "/d/f.txt") == "file"
    assert path_kind(local_ws, "nope") is None
    ws = FakeWorkspace(files={"top.txt": "x"})
    assert path_kind(ws, "top.txt") == "file"


def test_path_kind_defaults_to_file_when_parent_listing_omits_it():
    class Odd(FakeWorkspace):
        def exists(self, path):
            return True

        def list_files(self, path=".", depth=1):
            return []

    assert path_kind(Odd(), "ghost") == "file"


# ── pagination helpers ───────────────────────────────────────────────────────

def test_apply_head_limit():
    items = [str(i) for i in range(10)]
    assert apply_head_limit(items, None, 0) == (items, None)
    assert apply_head_limit(items, 3, 0) == (["0", "1", "2"], 3)
    assert apply_head_limit(items, 3, 8) == (["8", "9"], None)
    assert apply_head_limit(items, 3, 7) == (["7", "8", "9"], None)
    assert apply_head_limit(items, 0, 4) == (items[4:], None)
    big = [str(i) for i in range(DEFAULT_HEAD_LIMIT + 1)]
    assert apply_head_limit(big, None, 0) == (big[:DEFAULT_HEAD_LIMIT], DEFAULT_HEAD_LIMIT)
    exact = [str(i) for i in range(DEFAULT_HEAD_LIMIT)]
    assert apply_head_limit(exact, None, 0) == (exact, None)


def test_format_limit_info():
    assert format_limit_info(None, 0) == ""
    assert format_limit_info(5, 0) == "limit: 5"
    assert format_limit_info(None, 3) == "offset: 3"
    assert format_limit_info(5, 3) == "limit: 5, offset: 3"


# ── edit helpers ─────────────────────────────────────────────────────────────

def test_normalize_quotes():
    assert normalize_quotes(f"{L1}a{R1} {L2}b{R2}") == "'a' \"b\""


def test_find_actual_string():
    assert find_actual_string("say 'hi'", "'hi'") == "'hi'"
    assert find_actual_string(f"say {L2}hi{R2} now", '"hi"') == f"{L2}hi{R2}"
    assert find_actual_string("abc", "zzz") is None
    assert find_actual_string("abc", "") == ""


def test_preserve_quote_style():
    assert preserve_quote_style("x", "x", '"n"') == '"n"'
    assert preserve_quote_style('"a"', f"{L2}a{R2}", '"b" (and "c")') == f"{L2}b{R2} (and {L2}c{R2})"
    assert preserve_quote_style("'a'", f"{L1}a{R1}", "don't 'go'") == f"don{R1}t {L1}go{R1}"
    # normalized match but no curly quotes in the actual text: unchanged
    assert preserve_quote_style("ab", "aB", '"x"') == '"x"'


@pytest.mark.parametrize("prev", [" ", "\t", "\n", "\r", "(", "[", "{", "—", "–"])
def test_opening_contexts(prev):
    assert preserve_quote_style('"a"', f"{L2}a{R2}", f'{prev}"x"') == f"{prev}{L2}x{R2}"


def test_single_quote_closing_after_letter_and_at_end():
    assert preserve_quote_style("'a'", f"{L1}a{R1}", "a' b") == f"a{R1} b"
    assert preserve_quote_style("'a'", f"{L1}a{R1}", "'") == L1


def test_apply_edit_to_file():
    assert apply_edit_to_file("a b a", "a", "c", False) == "c b a"
    assert apply_edit_to_file("a b a", "a", "c", True) == "c b c"
    # deleting a whole line removes its newline too
    assert apply_edit_to_file("x\ny\nz\n", "y", "", False) == "x\nz\n"
    assert apply_edit_to_file("y\ny\n", "y", "", True) == ""
    assert apply_edit_to_file("x\ny\n", "y\n", "", False) == "x\n"
    assert apply_edit_to_file("xy", "y", "", False) == "x"


def test_add_line_numbers():
    assert add_line_numbers(["a", "b"], 1) == "1\ta\n2\tb"
    assert add_line_numbers(["c"], 7) == "7\tc"
    assert add_line_numbers([], 1) == ""


# ── prompts and metadata ─────────────────────────────────────────────────────

@pytest.mark.parametrize("builder", ALL_BUILDERS)
def test_prompt_frontmatter_is_stripped(builder):
    prompt = builder().prompt
    assert not prompt.startswith("---")
    assert "name:" not in prompt.splitlines()[0]
    assert prompt.strip()


def test_get_prompt():
    assert get_prompt("does-not-exist.md", "fallback text") == "fallback text"
    assert get_prompt("glob.md", "x").startswith("Fast file pattern matching")


def test_read_only_tools_are_read_only_and_concurrency_safe():
    for builder in (build_read_file_tool, build_glob_tool, build_grep_tool):
        tool = builder()
        assert tool.is_read_only({}) is True
        assert tool.is_concurrency_safe({}) is True
        assert tool.is_destructive({}) is False


def test_writers_are_destructive_not_read_only_and_not_concurrency_safe():
    for builder in (build_write_file_tool, build_edit_tool):
        tool = builder()
        assert tool.is_read_only({}) is False
        assert tool.is_concurrency_safe({}) is False
        assert tool.is_destructive({}) is True


def test_generic_writers_do_not_claim_phase_flags():
    """Claiming either flag gets the tool stripped from the pool in that phase."""
    for builder in (build_write_file_tool, build_edit_tool):
        tool = builder()
        assert tool.is_implementation_writer() is False
        assert tool.is_test_writer() is False


@pytest.mark.parametrize("phase", list(TddPhase))
def test_write_primitives_survive_pool_assembly_in_every_phase(phase):
    pool = assemble_tool_pool([b() for b in ALL_BUILDERS], phase_ledger=PhaseLedger(phase=phase))
    assert {"WriteFile", "Edit", "ReadFile", "Glob", "Grep"} <= {t.name for t in pool}


def test_runtime_gate_classifies_write_primitives_by_path():
    write, edit = build_write_file_tool(), build_edit_tool()
    red, green = PhaseLedger(phase=TddPhase.RED), PhaseLedger(phase=TddPhase.GREEN)
    assert check_tdd_phase_permission(write, {"file_path": "tests/test_a.py"}, red)[0] is True
    assert check_tdd_phase_permission(write, {"file_path": "src/a.py"}, red)[0] is False
    assert check_tdd_phase_permission(edit, {"file_path": "src/a.py"}, green)[0] is True
    assert check_tdd_phase_permission(edit, {"file_path": "tests/test_a.py"}, green)[0] is False


@pytest.mark.parametrize("builder, name, args, expected", [
    (build_read_file_tool, "ReadFile", {"file_path": "a"}, "Read file: a"),
    (build_write_file_tool, "WriteFile", {"file_path": "a"}, "Write file: a"),
    (build_edit_tool, "Edit", {"file_path": "a"}, "Edit file: a"),
    (build_glob_tool, "Glob", {"pattern": "*.py"}, "Glob: *.py"),
    (build_grep_tool, "Grep", {"pattern": "x"}, "Grep: x"),
])
def test_names_and_descriptions(builder, name, args, expected):
    tool = builder()
    assert tool.name == name
    assert tool.description(args) == expected


def test_schemas_follow_sdk_tools_contract():
    def props(builder):
        return set(builder().input_schema["properties"])

    assert build_read_file_tool().input_schema["required"] == ["file_path"]
    assert props(build_read_file_tool) == {"file_path", "offset", "limit"}
    assert build_write_file_tool().input_schema["required"] == ["file_path", "content"]
    assert build_edit_tool().input_schema["required"] == ["file_path", "old_string", "new_string"]
    assert props(build_edit_tool) == {"file_path", "old_string", "new_string", "replace_all"}
    assert build_glob_tool().input_schema["required"] == ["pattern"]
    assert props(build_glob_tool) == {"pattern", "path"}
    assert build_grep_tool().input_schema["required"] == ["pattern"]
    assert props(build_grep_tool) == {
        "pattern", "path", "glob", "output_mode", "-B", "-A", "-C", "context", "-n", "-i", "type",
        "head_limit", "offset", "multiline"}
    assert build_grep_tool().input_schema["properties"]["output_mode"]["enum"] == [
        "content", "files_with_matches", "count"]


@pytest.mark.parametrize("builder, args", [
    (build_read_file_tool, {"file_path": "a"}),
    (build_write_file_tool, {"file_path": "a", "content": ""}),
    (build_edit_tool, {"file_path": "a", "old_string": "x", "new_string": "y"}),
    (build_glob_tool, {"pattern": "*"}),
    (build_grep_tool, {"pattern": "x"}),
])
def test_every_tool_errors_without_workspace(builder, args):
    assert validate(builder, args, ctx_for()).valid is True
    res = run(builder().call(args, ctx_for()))
    assert res.is_error is True
    assert res.content == "No workspace available"


# ── ReadFile ─────────────────────────────────────────────────────────────────

def test_read_numbers_lines_from_one_and_records_state():
    ws = FakeWorkspace(files={"f.txt": "a\nb\n"})
    ctx = ctx_for(ws)
    res = read(ctx, "f.txt")
    assert res.is_error is False
    assert res.content == "1\ta\n2\tb"
    assert ctx.read_file_state["f.txt"] == FileState(content="a\nb\n", offset=None, limit=None)


def test_read_offset_is_one_based_and_zero_means_start():
    ws = FakeWorkspace(files={"f.txt": "l1\nl2\nl3\nl4\n"})
    ctx = ctx_for(ws)
    assert read(ctx, "f.txt", offset=2, limit=2).content == "2\tl2\n3\tl3"
    assert read(ctx, "f.txt", offset=4).content == "4\tl4"
    assert read(ctx, "f.txt", offset=1).content.startswith("1\tl1")
    assert read(ctx, "f.txt", offset=0, limit=1).content == "1\tl1"
    assert read(ctx, "f.txt", limit=1).content == "1\tl1"


def test_ranged_read_records_full_content_with_range():
    ws = FakeWorkspace(files={"f.txt": "a\nb\nc\n"})
    ctx = ctx_for(ws)
    read(ctx, "f.txt", offset=2, limit=1)
    assert ctx.read_file_state["f.txt"] == FileState(content="a\nb\nc\n", offset=2, limit=1)
    read(ctx, "f.txt", limit=1)
    assert ctx.read_file_state["f.txt"] == FileState(content="a\nb\nc\n", offset=1, limit=1)


def test_read_state_key_is_normalized_and_crlf_is_normalized():
    ws = FakeWorkspace(files={"d/f.txt": "a\r\nb"})
    ctx = ctx_for(ws)
    assert read(ctx, "/d/./f.txt").content == "1\ta\n2\tb"
    assert ctx.read_file_state["d/f.txt"].content == "a\nb"


def test_read_default_limit_is_applied():
    ws = FakeWorkspace(files={"big.txt": "".join(f"{i}\n" for i in range(MAX_LINES_TO_READ + 5))})
    out = read(ctx_for(ws), "big.txt").content.split("\n")
    assert len(out) == MAX_LINES_TO_READ
    assert out[-1] == f"{MAX_LINES_TO_READ}\t{MAX_LINES_TO_READ - 1}"


def test_read_empty_file_reminder():
    ctx = ctx_for(FakeWorkspace(files={"e.txt": ""}))
    res = read(ctx, "e.txt")
    assert res.is_error is False
    assert res.content == "<system-reminder>Warning: the file exists but the contents are empty.</system-reminder>"
    assert ctx.read_file_state["e.txt"].content == ""


def test_read_shorter_than_offset_reminder():
    res = read(ctx_for(FakeWorkspace(files={"f.txt": "a\nb\n"})), "f.txt", offset=5)
    assert res.is_error is False
    assert res.content == ("<system-reminder>Warning: the file exists but is shorter than the provided "
                           "offset (5). The file has 2 lines.</system-reminder>")


def test_read_whitespace_only_lines_are_content():
    assert read(ctx_for(FakeWorkspace(files={"w.txt": "  \n"})), "w.txt").content == "1\t  "


def test_read_missing_file():
    ctx = ctx_for(FakeWorkspace())
    res = read(ctx, "nope.txt")
    assert res.is_error is True
    assert res.content == f"File does not exist. {FILE_NOT_FOUND_CWD_NOTE}"
    assert ctx.read_file_state == {}


def test_read_other_error():
    class Boom(FakeWorkspace):
        def read_file(self, path):
            raise RuntimeError("disk on fire")

    res = read(ctx_for(Boom()), "f")
    assert res.is_error is True
    assert res.content == "Error reading file: disk on fire"


@pytest.mark.parametrize("args, message", [
    ({"offset": -1}, "offset must be a non-negative integer."),
    ({"offset": "1"}, "offset must be a non-negative integer."),
    ({"offset": True}, "offset must be a non-negative integer."),
    ({"offset": None}, "offset must be a non-negative integer."),
    ({"limit": 0}, "limit must be a positive integer."),
    ({"limit": -3}, "limit must be a positive integer."),
    ({"limit": 1.5}, "limit must be a positive integer."),
    ({"limit": False}, "limit must be a positive integer."),
    ({"limit": None}, "limit must be a positive integer."),
])
def test_read_rejects_bad_ranges(args, message):
    res = validate(build_read_file_tool, {"file_path": "f.txt", **args}, ctx_for(FakeWorkspace()))
    assert res.valid is False
    assert res.message == message
    assert res.error_code == 0


def test_read_accepts_valid_ranges():
    for args in ({}, {"offset": 0}, {"offset": 3, "limit": 1}):
        assert validate(build_read_file_tool, {"file_path": "f", **args}, ctx_for(FakeWorkspace())).valid


# ── WriteFile ────────────────────────────────────────────────────────────────

def test_write_creates_new_file_without_prior_read(local_ws, tmp_path):
    ctx = ctx_for(local_ws)
    args = {"file_path": "pkg/hello.txt", "content": "Hello\nLine 2"}
    assert validate(build_write_file_tool, args, ctx).valid is True
    res = run(build_write_file_tool().call(args, ctx))
    assert res.is_error is False
    assert res.content == "File created successfully at: pkg/hello.txt"
    assert (tmp_path / "pkg" / "hello.txt").read_text() == "Hello\nLine 2"
    assert ctx.read_file_state["pkg/hello.txt"] == FileState(content="Hello\nLine 2")


def test_write_existing_file_requires_read():
    ws = FakeWorkspace(files={"a.txt": "old"})
    ctx = ctx_for(ws)
    res = validate(build_write_file_tool, {"file_path": "a.txt", "content": "new"}, ctx)
    assert (res.valid, res.message, res.error_code) == (False, FILE_NOT_READ_ERROR, 2)


def test_write_partial_view_counts_as_unread():
    ws = FakeWorkspace(files={"a.txt": "old"})
    ctx = ctx_for(ws)
    ctx.read_file_state["a.txt"] = FileState(content="old", is_partial_view=True)
    res = validate(build_write_file_tool, {"file_path": "a.txt", "content": "new"}, ctx)
    assert (res.valid, res.error_code) == (False, 2)


def test_write_existing_file_modified_since_read():
    ws = FakeWorkspace(files={"a.txt": "old"})
    ctx = ctx_for(ws)
    read(ctx, "a.txt")
    ws.files["a.txt"] = "changed by linter"
    res = validate(build_write_file_tool, {"file_path": "a.txt", "content": "new"}, ctx)
    assert (res.valid, res.message, res.error_code) == (False, FILE_MODIFIED_SINCE_READ_ERROR, 3)


def test_write_existing_file_after_read_updates():
    ws = FakeWorkspace(files={"a.txt": "old"})
    ctx = ctx_for(ws)
    read(ctx, "a.txt")
    args = {"file_path": "a.txt", "content": "new"}
    assert validate(build_write_file_tool, args, ctx).valid is True
    res = run(build_write_file_tool().call(args, ctx))
    assert res.content == "The file a.txt has been updated successfully."
    assert ws.files["a.txt"] == "new"
    # the write refreshes the state, so a second write needs no new Read
    assert validate(build_write_file_tool, {"file_path": "a.txt", "content": "newer"}, ctx).valid is True


def test_write_call_rechecks_staleness():
    ws = FakeWorkspace(files={"a.txt": "old"})
    ctx = ctx_for(ws)
    read(ctx, "a.txt")
    ws.files["a.txt"] = "changed"
    res = run(build_write_file_tool().call({"file_path": "a.txt", "content": "new"}, ctx))
    assert res.is_error is True
    assert res.content == FILE_UNEXPECTEDLY_MODIFIED_ERROR
    assert ws.files["a.txt"] == "changed"


def test_write_through_pipeline_surfaces_validation_error():
    ctx = ctx_for(FakeWorkspace(files={"a.txt": "old"}))
    msg = via_pipeline(build_write_file_tool, {"file_path": "a.txt", "content": "x"}, ctx)
    assert msg.status == "error"
    assert msg.content == f"<tool_use_error>{FILE_NOT_READ_ERROR}</tool_use_error>"
    assert msg.additional_kwargs["error_code"] == 2


def test_write_defaults_content_to_empty():
    ws = FakeWorkspace()
    run(build_write_file_tool().call({"file_path": "a.txt"}, ctx_for(ws)))
    assert ws.files["a.txt"] == ""


def test_write_failure_is_an_error_result():
    ws = FakeWorkspace()
    ws.fail_write_on.add("a.txt")
    ctx = ctx_for(ws)
    res = run(build_write_file_tool().call({"file_path": "a.txt", "content": "x"}, ctx))
    assert res.is_error is True
    assert res.content == "Error writing file: Injected write failure for 'a.txt'"
    assert ctx.read_file_state == {}


def test_write_cannot_escape_workspace(local_ws, tmp_path):
    res = run(build_write_file_tool().call({"file_path": "../escape.txt", "content": "x"}, ctx_for(local_ws)))
    assert res.is_error is True
    assert not (tmp_path.parent / "escape.txt").exists()


# ── Edit ─────────────────────────────────────────────────────────────────────

def edit_args(path="f.txt", old="a", new="b", **kw):
    return {"file_path": path, "old_string": old, "new_string": new, **kw}


def read_ctx(files, path="f.txt"):
    ws = FakeWorkspace(files=files)
    ctx = ctx_for(ws)
    read(ctx, path)
    return ws, ctx


def test_edit_identical_strings_code_1():
    res = validate(build_edit_tool, edit_args(old="b", new="b"), ctx_for(FakeWorkspace()))
    assert (res.valid, res.error_code) == (False, 1)
    assert res.message == "No changes to make: old_string and new_string are exactly the same."


def test_edit_missing_file_code_4():
    res = validate(build_edit_tool, edit_args(path="nope"), ctx_for(FakeWorkspace()))
    assert (res.valid, res.error_code) == (False, 4)
    assert res.message == f"File does not exist. {FILE_NOT_FOUND_CWD_NOTE}"


def test_edit_empty_old_string_creates_missing_file():
    ws = FakeWorkspace()
    ctx = ctx_for(ws)
    args = edit_args(path="new.py", old="", new="print(1)\n")
    assert validate(build_edit_tool, args, ctx).valid is True
    res = run(build_edit_tool().call(args, ctx))
    assert res.is_error is False
    assert res.content == "The file new.py has been updated successfully."
    assert ws.files["new.py"] == "print(1)\n"
    assert ctx.read_file_state["new.py"] == FileState(content="print(1)\n")


def test_edit_empty_old_string_on_non_empty_file_code_3():
    res = validate(build_edit_tool, edit_args(old="", new="x"), ctx_for(FakeWorkspace(files={"f.txt": "abc"})))
    assert (res.valid, res.error_code) == (False, 3)
    assert res.message == "Cannot create new file - file already exists."


def test_edit_empty_old_string_on_empty_file_validates_but_call_requires_read():
    ws = FakeWorkspace(files={"f.txt": "  \n"})
    ctx = ctx_for(ws)
    args = edit_args(old="", new="x")
    assert validate(build_edit_tool, args, ctx).valid is True
    res = run(build_edit_tool().call(args, ctx))
    assert (res.is_error, res.content) == (True, FILE_UNEXPECTEDLY_MODIFIED_ERROR)
    read(ctx, "f.txt")
    assert run(build_edit_tool().call(args, ctx)).is_error is False
    assert ws.files["f.txt"] == "x"


def test_edit_unread_file_code_6_and_modified_code_7():
    ws = FakeWorkspace(files={"f.txt": "abc"})
    ctx = ctx_for(ws)
    res = validate(build_edit_tool, edit_args(), ctx)
    assert (res.valid, res.message, res.error_code) == (False, FILE_NOT_READ_ERROR, 6)
    read(ctx, "f.txt")
    ws.files["f.txt"] = "abcd"
    res = validate(build_edit_tool, edit_args(), ctx)
    assert (res.valid, res.message, res.error_code) == (False, FILE_MODIFIED_SINCE_READ_ERROR, 7)


def test_edit_after_ranged_read_is_allowed():
    ws = FakeWorkspace(files={"f.txt": "a\nb\n"})
    ctx = ctx_for(ws)
    read(ctx, "f.txt", offset=2, limit=1)
    assert validate(build_edit_tool, edit_args(old="b", new="c"), ctx).valid is True


def test_edit_not_found_code_8():
    _, ctx = read_ctx({"f.txt": "A\n"})
    res = validate(build_edit_tool, edit_args(old="X", new="Y"), ctx)
    assert (res.valid, res.error_code) == (False, 8)
    assert res.message == "String to replace not found in file.\nString: X"


def test_edit_multiple_matches_code_9_unless_replace_all():
    ws, ctx = read_ctx({"f.txt": "a a a"})
    res = validate(build_edit_tool, edit_args(), ctx)
    assert (res.valid, res.error_code) == (False, 9)
    assert res.message == (
        "Found 3 matches of the string to replace, but replace_all is false. To replace all "
        "occurrences, set replace_all to true. To replace only one occurrence, please provide "
        "more context to uniquely identify the instance.\nString: a")
    args = edit_args(replace_all=True)
    assert validate(build_edit_tool, args, ctx).valid is True
    res = run(build_edit_tool().call(args, ctx))
    assert res.content == "The file f.txt has been updated. All occurrences were successfully replaced."
    assert ws.files["f.txt"] == "b b b"


def test_edit_single_match_success_and_state_refresh(local_ws, tmp_path):
    (tmp_path / "edit.txt").write_text("A\nB\nC\n")
    ctx = ctx_for(local_ws)
    read(ctx, "edit.txt")
    args = edit_args(path="edit.txt", old="B\n", new="X\n", replace_all=False)
    assert validate(build_edit_tool, args, ctx).valid is True
    res = run(build_edit_tool().call(args, ctx))
    assert res.is_error is False
    assert res.content == "The file edit.txt has been updated successfully."
    assert (tmp_path / "edit.txt").read_text() == "A\nX\nC\n"
    assert ctx.read_file_state["edit.txt"] == FileState(content="A\nX\nC\n")
    # consecutive edits need no fresh Read
    assert validate(build_edit_tool, edit_args(path="edit.txt", old="X", new="Y"), ctx).valid is True


def test_edit_curly_quote_normalization_end_to_end():
    ws, ctx = read_ctx({"f.txt": f"say {L2}hello{R2}\n"})
    args = edit_args(old='"hello"', new='"bye"')
    assert validate(build_edit_tool, args, ctx).valid is True
    run(build_edit_tool().call(args, ctx))
    assert ws.files["f.txt"] == f"say {L2}bye{R2}\n"


def test_edit_deleting_a_line_drops_its_newline():
    ws, ctx = read_ctx({"f.txt": "x\ny\nz\n"})
    run(build_edit_tool().call(edit_args(old="y", new=""), ctx))
    assert ws.files["f.txt"] == "x\nz\n"


def test_edit_defaults_new_string_to_empty():
    ws, ctx = read_ctx({"f.txt": "abc"})
    run(build_edit_tool().call({"file_path": "f.txt", "old_string": "b"}, ctx))
    assert ws.files["f.txt"] == "ac"


def test_edit_call_rechecks_staleness():
    ws, ctx = read_ctx({"f.txt": "abc"})
    ws.files["f.txt"] = "abX"
    res = run(build_edit_tool().call(edit_args(), ctx))
    assert (res.is_error, res.content) == (True, FILE_UNEXPECTEDLY_MODIFIED_ERROR)
    assert ws.files["f.txt"] == "abX"


def test_edit_write_failure():
    ws, ctx = read_ctx({"f.txt": "abc"})
    ws.fail_write_on.add("f.txt")
    res = run(build_edit_tool().call(edit_args(), ctx))
    assert res.is_error is True
    assert res.content == "Error editing file: Injected write failure for 'f.txt'"
    assert ctx.read_file_state["f.txt"].content == "abc"


def test_edit_through_pipeline():
    """Validation runs before the TDD gate; RED permits test files only."""
    path = "tests/test_f.py"
    ws = FakeWorkspace(files={path: "abc", "src/m.py": "abc"})
    ctx = ctx_for(ws)
    msg = via_pipeline(build_edit_tool, edit_args(path=path), ctx)
    assert msg.content == f"<tool_use_error>{FILE_NOT_READ_ERROR}</tool_use_error>"
    assert msg.additional_kwargs["error_code"] == 6
    via_pipeline(build_read_file_tool, {"file_path": path}, ctx)
    msg = via_pipeline(build_edit_tool, edit_args(path=path), ctx)
    assert msg.status == "success"
    assert ws.files[path] == "bbc"
    via_pipeline(build_read_file_tool, {"file_path": "src/m.py"}, ctx)
    msg = via_pipeline(build_edit_tool, edit_args(path="src/m.py"), ctx)
    assert msg.status == "error"
    assert "TDD phase RED denies" in msg.content
    assert ws.files["src/m.py"] == "abc"


# ── Glob (real ripgrep, the vendored wheel) ─────────────────────────────────

def make_tree(root, files, mtimes=None):
    """Write files; `mtimes` maps path -> epoch seconds so --sort=modified is deterministic."""
    for i, (rel, content) in enumerate(files.items()):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        t = (mtimes or {}).get(rel, 1_000_000 + i)
        os.utime(target, (t, t))


def glob(ws, **args):
    return run(build_glob_tool().call(args, ctx_for(ws)))


def test_glob_sorted_oldest_first_with_relative_paths(local_ws, tmp_path):
    make_tree(tmp_path, {"src/z.py": "", "src/a.py": "", "top.py": "", "src/notes.txt": ""},
              {"src/z.py": 1_000_000, "src/a.py": 3_000_000, "top.py": 2_000_000})
    res = glob(local_ws, pattern="**/*.py")
    assert res.is_error is False
    assert res.content == "src/z.py\ntop.py\nsrc/a.py"


def test_glob_slashless_pattern_matches_any_depth(local_ws, tmp_path):
    make_tree(tmp_path, {"a.py": "", "d/e/b.py": "", "c.txt": ""})
    assert glob(local_ws, pattern="*.py").content == "a.py\nd/e/b.py"


def test_glob_includes_hidden_and_ignored_files(local_ws, tmp_path):
    make_tree(tmp_path, {".gitignore": "ignored.py\n", "ignored.py": "", ".hidden/h.py": ""})
    (tmp_path / ".git").mkdir()
    assert glob(local_ws, pattern="*.py").content == "ignored.py\n.hidden/h.py"


def test_glob_path_scopes_search(local_ws, tmp_path):
    make_tree(tmp_path, {"src/a.py": "", "src/sub/b.py": "", "lib/c.py": ""})
    assert glob(local_ws, pattern="*.py", path="src").content == "src/a.py\nsrc/sub/b.py"
    # rg matches a slashed glob against the cwd-relative path, not the search path
    assert glob(local_ws, pattern="sub/*.py", path="src").content == "No files found"
    assert glob(local_ws, pattern="src/sub/*.py", path="src").content == "src/sub/b.py"


def test_glob_absolute_pattern_is_rerooted_at_workspace(local_ws, tmp_path):
    make_tree(tmp_path, {"src/a.py": "", "lib/b.py": ""})
    assert glob(local_ws, pattern="/src/*.py").content == "src/a.py"
    assert glob(local_ws, pattern="/*.py").content == "src/a.py\nlib/b.py"


def test_glob_validate_path(local_ws, tmp_path):
    make_tree(tmp_path, {"d/x": "", "f.txt": "x"})
    ctx = ctx_for(local_ws)
    assert validate(build_glob_tool, {"pattern": "*", "path": "d"}, ctx).valid is True
    assert validate(build_glob_tool, {"pattern": "*"}, ctx).valid is True
    assert validate(build_glob_tool, {"pattern": "*", "path": ""}, ctx).valid is True
    res = validate(build_glob_tool, {"pattern": "*", "path": "nope"}, ctx)
    assert (res.valid, res.error_code) == (False, 1)
    assert res.message == f"Directory does not exist: nope. {FILE_NOT_FOUND_CWD_NOTE}"
    res = validate(build_glob_tool, {"pattern": "*", "path": "f.txt"}, ctx)
    assert (res.valid, res.message, res.error_code) == (False, "Path is not a directory: f.txt", 2)


def test_glob_truncates_at_upstream_limit(local_ws, tmp_path):
    make_tree(tmp_path, {f"f{i:03}.py": "" for i in range(GLOB_MAX_RESULTS + 1)})
    lines = glob(local_ws, pattern="*.py").content.split("\n")
    assert len(lines) == GLOB_MAX_RESULTS + 1
    assert lines[-1] == "(Results are truncated. Consider using a more specific path or pattern.)"
    assert lines[-2] == f"f{GLOB_MAX_RESULTS - 1:03}.py"


def test_glob_exactly_at_limit_is_not_truncated(local_ws, tmp_path):
    make_tree(tmp_path, {f"f{i:03}.py": "" for i in range(GLOB_MAX_RESULTS)})
    lines = glob(local_ws, pattern="*.py").content.split("\n")
    assert len(lines) == GLOB_MAX_RESULTS
    assert lines[-1] == f"f{GLOB_MAX_RESULTS - 1:03}.py"


def test_glob_no_matches(local_ws, tmp_path):
    make_tree(tmp_path, {"a.py": ""})
    assert glob(local_ws, pattern="*.rs").content == "No files found"


def test_glob_cannot_see_outside_workspace(tmp_path):
    root = tmp_path / "root"
    make_tree(root, {"inside.py": ""})
    make_tree(tmp_path, {"secret.py": ""})
    ws = LocalWorkspace(str(root))
    assert glob(ws, pattern="../*.py").content == "No files found"
    assert glob(ws, pattern="**/*.py").content == "inside.py"


def test_glob_does_not_follow_symlinks(tmp_path):
    make_tree(tmp_path / "outside", {"secret.py": ""})
    root = tmp_path / "root"
    root.mkdir()
    os.symlink(tmp_path / "outside", root / "link")
    assert glob(LocalWorkspace(str(root)), pattern="**/*.py").content == "No files found"


def test_glob_runs_upstream_argv(monkeypatch):
    seen = {}

    def fake_rip_grep(ws, args, target):
        seen["args"], seen["target"] = args, target
        return ["./b", "a"]

    monkeypatch.setattr(fs, "rip_grep", fake_rip_grep)
    res = glob(FakeWorkspace(), pattern="*.py", path="/src/")
    assert seen == {"args": ["--files", "--glob", "*.py", "--sort=modified", "--no-ignore", "--hidden"],
                    "target": "src"}
    assert res.content == "b\na"
    glob(FakeWorkspace(), pattern="x")
    assert seen["target"] == "."


def test_glob_ripgrep_failure_is_an_error(monkeypatch):
    def boom(ws, args, target):
        raise RuntimeError("rg missing")

    monkeypatch.setattr(fs, "rip_grep", boom)
    res = glob(FakeWorkspace(), pattern="*")
    assert (res.is_error, res.content) == (True, "Glob error: rg missing")


@pytest.mark.parametrize("pattern, expected", [
    ("/src/*.py", ("/src", "*.py")),
    ("/*.txt", ("/", "*.txt")),
    ("/a/b/**/c.py", ("/a/b", "**/c.py")),
    ("/a/b/c.py", ("/a/b", "c.py")),
    ("/c.py", ("/", "c.py")),
    ("c.py", (".", "c.py")),
    ("*.py", ("", "*.py")),
    ("/a/{x,y}/z", ("/a", "{x,y}/z")),
    ("/a/b?", ("/a", "b?")),
    ("/a/[bc]", ("/a", "[bc]")),
])
def test_extract_glob_base_directory(pattern, expected):
    assert fs.extract_glob_base_directory(pattern) == expected


# ── Grep (real ripgrep) ──────────────────────────────────────────────────────

def grep(ws, **args):
    return run(build_grep_tool().call(args, ctx_for(ws)))


def test_grep_default_mode_sorts_files_newest_first(local_ws, tmp_path):
    make_tree(tmp_path, {"b.py": "hit", "a.py": "hit\nhit", "c.py": "miss", "d.py": "hit"},
              {"b.py": 2_000_000, "a.py": 1_000_000, "d.py": 2_000_000})
    assert grep(local_ws, pattern="hit").content == "Found 3 files\nb.py\nd.py\na.py"
    assert grep(local_ws, pattern="hit", glob="a.py").content == "Found 1 file\na.py"
    assert grep(local_ws, pattern="zzz").content == "No files found"


def test_grep_content_mode_with_and_without_line_numbers(local_ws, tmp_path):
    make_tree(tmp_path, {"src/m.py": "def foo():\n    print('hello grep')\n", "x.py": ""})
    assert grep(local_ws, pattern="hello grep", output_mode="content").content == "src/m.py:2:    print('hello grep')"
    res = grep(local_ws, pattern="hello grep", output_mode="content", **{"-n": False})
    assert res.content == "src/m.py:    print('hello grep')"
    assert grep(local_ws, pattern="zzz", output_mode="content").content == "No matches found"


def test_grep_count_mode(local_ws, tmp_path):
    make_tree(tmp_path, {"a.py": "x x\nx\ny", "b.py": "x"})
    res = grep(local_ws, pattern="x", output_mode="count")
    assert sorted(res.content.split("\n\n")[0].split("\n")) == ["a.py:2", "b.py:1"]
    assert res.content.endswith("\n\nFound 3 total occurrences across 2 files.")
    res = grep(local_ws, pattern="x", output_mode="count", glob="b.py")
    assert res.content == "b.py:1\n\nFound 1 total occurrence across 1 file."
    res = grep(local_ws, pattern="zzz", output_mode="count")
    assert res.content == "No matches found\n\nFound 0 total occurrences across 0 files."


def test_grep_case_insensitive_and_type(local_ws, tmp_path):
    make_tree(tmp_path, {"a.py": "Hello", "b.rs": "Hello"})
    assert grep(local_ws, pattern="hello").content == "No files found"
    assert grep(local_ws, pattern="hello", type="py", **{"-i": True}).content == "Found 1 file\na.py"


def test_grep_glob_lists(local_ws, tmp_path):
    make_tree(tmp_path, {"a.py": "hit", "a.txt": "hit", "d/c.ts": "hit"})
    res = grep(local_ws, pattern="hit", glob="*.{ts,txt}")
    assert sorted(res.content.split("\n")[1:]) == ["a.txt", "d/c.ts"]
    res = grep(local_ws, pattern="hit", glob="*.txt,*.ts")
    assert sorted(res.content.split("\n")[1:]) == ["a.txt", "d/c.ts"]


def test_grep_single_file_target_has_no_filename_prefix(local_ws, tmp_path):
    """rg omits the path for a single-file target; upstream passes that through."""
    make_tree(tmp_path, {"src/a.py": "hit", "src/b.py": "hit"})
    assert grep(local_ws, pattern="hit", path="src/a.py", output_mode="content").content == "1:hit"


def test_grep_excludes_vcs_directories(local_ws, tmp_path):
    files = {f"{d}/x": "hit" for d in VCS_DIRECTORIES_TO_EXCLUDE}
    files["ok.txt"] = "hit"
    make_tree(tmp_path, files)
    assert grep(local_ws, pattern="hit").content == "Found 1 file\nok.txt"


def test_grep_searches_hidden_files(local_ws, tmp_path):
    make_tree(tmp_path, {".env.example": "hit"})
    assert grep(local_ws, pattern="hit").content == "Found 1 file\n.env.example"


def test_grep_long_lines_are_omitted(local_ws, tmp_path):
    make_tree(tmp_path, {"a": "x" * MAX_COLUMNS + "hit hit\n" + "z" * (MAX_COLUMNS + 1) + "\nhit"})
    res = grep(local_ws, pattern="hit", output_mode="content", **{"-B": 1})
    # upstream relativizes only the first-colon path; context lines keep rg's raw prefix
    assert res.content == "a:1:[Omitted long matching line]\n./a-2-[Omitted long context line]\na:3:hit"


CTX_FILE = "l1\nl2\nhit3\nl4\nl5\nl6\nhit7\nl8\n"


def test_grep_context_flags(local_ws, tmp_path):
    make_tree(tmp_path, {"a": CTX_FILE})
    assert grep(local_ws, pattern="hit", output_mode="content", **{"-A": 1}).content == (
        "a:3:hit3\n./a-4-l4\n--\na:7:hit7\n./a-8-l8")
    assert grep(local_ws, pattern="hit", output_mode="content", **{"-B": 1}).content == (
        "./a-2-l2\na:3:hit3\n--\n./a-6-l6\na:7:hit7")
    assert grep(local_ws, pattern="hit3", output_mode="content", context=1, **{"-C": 3, "-A": 5}).content == (
        "./a-2-l2\na:3:hit3\n./a-4-l4")
    assert grep(local_ws, pattern="hit3", output_mode="content", **{"-C": 1, "-B": 0}).content == (
        "./a-2-l2\na:3:hit3\n./a-4-l4")


def test_grep_multiline(local_ws, tmp_path):
    make_tree(tmp_path, {"a": "def f(\n    x,\n):\n    pass\n"})
    assert grep(local_ws, pattern=r"def f\(.*?\):", output_mode="content").content == "No matches found"
    res = grep(local_ws, pattern=r"def f\(.*?\):", output_mode="content", multiline=True)
    assert res.content == "a:1:def f(\na:2:    x,\na:3:):"


def test_grep_pattern_starting_with_dash(local_ws, tmp_path):
    make_tree(tmp_path, {"a": "x --flag y"})
    assert grep(local_ws, pattern="--flag", output_mode="content").content == "a:1:x --flag y"


def test_grep_pagination(local_ws, tmp_path):
    make_tree(tmp_path, {"a": "\n".join(["hit"] * 5)})
    res = grep(local_ws, pattern="hit", output_mode="content", head_limit=2)
    assert res.content == "a:1:hit\na:2:hit\n\n[Showing results with pagination = limit: 2]"
    res = grep(local_ws, pattern="hit", output_mode="content", head_limit=2, offset=3)
    assert res.content == "a:4:hit\na:5:hit\n\n[Showing results with pagination = offset: 3]"
    assert grep(local_ws, pattern="hit", output_mode="content", head_limit=0).content.count("\n") == 4


def test_grep_pagination_files_and_count_modes(local_ws, tmp_path):
    make_tree(tmp_path, {f"f{i}": "hit" for i in range(4)}, {f"f{i}": 1_000_000 - i for i in range(4)})
    assert grep(local_ws, pattern="hit", head_limit=2).content == "Found 2 files limit: 2\nf0\nf1"
    assert grep(local_ws, pattern="hit", head_limit=2, offset=1).content == "Found 2 files limit: 2, offset: 1\nf1\nf2"
    res = grep(local_ws, pattern="hit", output_mode="count", head_limit=1)
    assert res.content.endswith(":1\n\nFound 1 total occurrence across 1 file. with pagination = limit: 1")


def test_grep_invalid_regex_returns_no_results_like_upstream(local_ws, tmp_path):
    """rg exits 2 with nothing on stdout; upstream resolves with the (empty) partial output."""
    make_tree(tmp_path, {"a": "("})
    assert grep(local_ws, pattern="(").content == "No files found"


def test_grep_validate(local_ws, tmp_path):
    make_tree(tmp_path, {"a": "x"})
    ctx = ctx_for(local_ws)
    assert validate(build_grep_tool, {"pattern": "x"}, ctx).valid is True
    assert validate(build_grep_tool, {"pattern": "x", "path": "a"}, ctx).valid is True
    res = validate(build_grep_tool, {"pattern": "x", "path": "nope"}, ctx)
    assert (res.valid, res.error_code) == (False, 1)
    assert res.message == f"Path does not exist: nope. {FILE_NOT_FOUND_CWD_NOTE}"
    for key in ("head_limit", "offset", "-A", "-B", "-C", "context"):
        for bad in (-1, "2", True, 1.5):
            res = validate(build_grep_tool, {"pattern": "x", key: bad}, ctx)
            assert (res.valid, res.message) == (False, f"{key} must be a non-negative integer.")
        assert validate(build_grep_tool, {"pattern": "x", key: 0}, ctx).valid is True
    res = validate(build_grep_tool, {"pattern": "x", "output_mode": "lines"}, ctx)
    assert (res.valid, res.message) == (False, "Invalid output_mode: lines")
    for mode in ("content", "files_with_matches", "count"):
        assert validate(build_grep_tool, {"pattern": "x", "output_mode": mode}, ctx).valid is True


VCS_GLOBS = [a for d in VCS_DIRECTORIES_TO_EXCLUDE for a in ("--glob", f"!{d}")]


@pytest.mark.parametrize("args, expected_tail", [
    ({"pattern": "p"}, ["-l", "p"]),
    ({"pattern": "p", "output_mode": "count"}, ["-c", "p"]),
    ({"pattern": "p", "output_mode": "content"}, ["-n", "p"]),
    ({"pattern": "p", "output_mode": "content", "-n": False}, ["p"]),
    ({"pattern": "p", "-n": True}, ["-l", "p"]),
    ({"pattern": "p", "output_mode": "content", "context": 2, "-C": 3, "-A": 1}, ["-n", "-C", "2", "p"]),
    ({"pattern": "p", "output_mode": "content", "-C": 3, "-A": 1}, ["-n", "-C", "3", "p"]),
    ({"pattern": "p", "output_mode": "content", "-B": 1, "-A": 2}, ["-n", "-B", "1", "-A", "2", "p"]),
    ({"pattern": "p", "output_mode": "content", "-B": 0}, ["-n", "-B", "0", "p"]),
    ({"pattern": "p", "-A": 2}, ["-l", "p"]),
    ({"pattern": "-x"}, ["-l", "-e", "-x"]),
    ({"pattern": "p", "type": "py"}, ["-l", "p", "--type", "py"]),
    ({"pattern": "p", "glob": "*.a *.{b,c},*.d"}, ["-l", "p", "--glob", "*.a", "--glob", "*.{b,c},*.d"]),
    ({}, ["-l", ""]),
])
def test_build_grep_args(args, expected_tail):
    assert fs.build_grep_args(args) == ["--hidden", *VCS_GLOBS, "--max-columns", "500", *expected_tail]


def test_build_grep_args_flags_order():
    args = fs.build_grep_args({"pattern": "p", "multiline": True, "-i": True})
    assert args[-5:] == ["-U", "--multiline-dotall", "-i", "-l", "p"]


def test_grep_count_mode_skips_unparseable_lines(monkeypatch):
    monkeypatch.setattr(fs, "rip_grep", lambda ws, a, t: ["./a:3", "weird", "b:x", "c:2"])
    res = grep(FakeWorkspace(), pattern="p", output_mode="count")
    assert res.content == "a:3\nweird\nb:x\nc:2\n\nFound 5 total occurrences across 2 files."


def test_grep_ripgrep_failure_is_an_error(monkeypatch):
    def boom(ws, args, target):
        raise RuntimeError("timed out")

    monkeypatch.setattr(fs, "rip_grep", boom)
    res = grep(FakeWorkspace(), pattern="p")
    assert (res.is_error, res.content) == (True, "Grep error: timed out")


def test_grep_target_is_normalized(monkeypatch):
    seen: dict[str, str] = {}
    monkeypatch.setattr(fs, "rip_grep", lambda ws, a, t: seen.setdefault("t", t) and [])
    grep(FakeWorkspace(), pattern="p", path="/src/")
    assert seen["t"] == "src"


def test_relativize_helpers():
    assert fs._relativize_first_colon("./a/b.py:3:x:y") == "a/b.py:3:x:y"
    assert fs._relativize_first_colon(":x") == ":x"
    assert fs._relativize_first_colon("nocolon") == "nocolon"
    assert fs._relativize_last_colon("./a:b:7") == "a:b:7"
    assert fs._relativize_last_colon(":7") == ":7"


class StatWorkspace(FakeWorkspace):
    def __init__(self, reply=None, raise_exc=False):
        super().__init__()
        self.reply, self.raise_exc = reply, raise_exc

    def execute(self, cmd, timeout=None, env=None):
        self.command_log.append(cmd)
        if self.raise_exc:
            raise RuntimeError("no python")
        from app.workspace.base import CommandResult
        return CommandResult(stdout=self.reply, stderr="", exit_code=0, duration=0.0, workspace="sandbox")


def test_sort_by_mtime_newest_first_with_filename_tiebreak():
    ws = StatWorkspace("5\n9\n5\n")
    assert fs.sort_by_mtime(ws, ["c", "a", "b"]) == ["a", "b", "c"]
    assert ws.command_log[0].startswith("python3 -c ")
    assert ws.command_log[0].endswith(" c a b")


def test_sort_by_mtime_falls_back_to_filename_order():
    assert fs.sort_by_mtime(StatWorkspace("1\n"), ["b", "a"]) == ["a", "b"]  # count mismatch
    assert fs.sort_by_mtime(StatWorkspace("x\ny\n"), ["b", "a"]) == ["a", "b"]  # unparseable
    assert fs.sort_by_mtime(StatWorkspace(raise_exc=True), ["b", "a"]) == ["a", "b"]
    ws = StatWorkspace("")
    assert fs.sort_by_mtime(ws, []) == []
    assert ws.command_log == []


def test_mtime_script_handles_missing_files(local_ws, tmp_path):
    make_tree(tmp_path, {"a": ""}, {"a": 1_234})
    assert fs.sort_by_mtime(local_ws, ["gone", "a"]) == ["a", "gone"]


# ── mutation-driven pins: exact schemas and edge behavior ────────────────────

EXPECTED_SCHEMAS = {
    "ReadFile": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "The path to the file to read"},
            "offset": {
                "type": "integer",
                "description": "The line number to start reading from. Only provide if the file is too large "
                "to read at once",
            },
            "limit": {
                "type": "integer",
                "description": "The number of lines to read. Only provide if the file is too large to read at once.",
            },
        },
        "required": ["file_path"],
    },
    "WriteFile": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "The path to the file to write"},
            "content": {"type": "string", "description": "The content to write to the file"},
        },
        "required": ["file_path", "content"],
    },
    "Edit": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "The path to the file to modify"},
            "old_string": {"type": "string", "description": "The text to replace"},
            "new_string": {"type": "string",
                           "description": "The text to replace it with (must be different from old_string)"},
            "replace_all": {"type": "boolean", "description": "Replace all occurrences of old_string (default false)"},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
    "Glob": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "The glob pattern to match files against"},
            "path": {"type": "string",
                     "description": "The directory to search in. If not specified, the workspace root is used."},
        },
        "required": ["pattern"],
    },
    "Grep": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string",
                        "description": "The regular expression pattern to search for in file contents"},
            "path": {"type": "string",
                     "description": "File or directory to search in (rg PATH). Defaults to the workspace root."},
            "glob": {"type": "string",
                     "description": 'Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}") - maps to rg --glob'},
            "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"],
                            "description": 'Output mode. Defaults to "files_with_matches".'},
            "-B": {"type": "integer", "description": "Lines to show before each match (rg -B). Content mode."},
            "-A": {"type": "integer", "description": "Lines to show after each match (rg -A). Content mode."},
            "-C": {"type": "integer", "description": "Alias for context."},
            "context": {"type": "integer",
                        "description": "Lines to show before and after each match (rg -C). Content mode."},
            "-n": {"type": "boolean", "description": "Show line numbers (rg -n). Content mode; defaults to true."},
            "-i": {"type": "boolean", "description": "Case insensitive search (rg -i)"},
            "type": {"type": "string",
                     "description": "File type to search (rg --type). Common types: js, py, rust, go, java, etc."},
            "head_limit": {"type": "integer",
                           "description": "Limit output to first N lines/entries. Defaults to 250; 0 for unlimited."},
            "offset": {"type": "integer",
                       "description": "Skip first N lines/entries before applying head_limit. Defaults to 0."},
            "multiline": {"type": "boolean",
                          "description": "Enable multiline mode where . matches newlines (rg -U --multiline-dotall)."},
        },
        "required": ["pattern"],
    },
}


@pytest.mark.parametrize("builder", ALL_BUILDERS)
def test_input_schemas_are_exact(builder):
    tool = builder()
    assert tool.input_schema == EXPECTED_SCHEMAS[tool.name]


def test_get_prompt_reads_utf8_body():
    assert "—" not in get_prompt("does-not-exist.md", "fallback")
    assert get_prompt("edit.md", "x").startswith("Performs exact string replacements in files.")


def test_split_grep_globs_needs_both_braces_to_keep_commas():
    assert split_grep_globs("a}b,c") == ["a}b", "c"]
    assert split_grep_globs("a{b,c") == ["a{b", "c"]


def test_path_kind_lists_the_parent_one_level_deep():
    calls = []

    class Spy(FakeWorkspace):
        def exists(self, path):
            return True

        def list_files(self, path=".", depth=1):
            calls.append((path, depth))
            from app.workspace.base import FileEntry
            return [FileEntry("a/b/c", True, 0)]

    assert path_kind(Spy(), "a/b/c") == "dir"
    assert calls == [("a/b", 1)]


def test_path_kind_top_level_dir_lists_root(local_ws, tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "x" / "y").mkdir(parents=True)
    assert path_kind(local_ws, "d") == "dir"
    assert path_kind(local_ws, "x/y") == "dir"


def test_find_actual_string_returns_first_normalized_match():
    text = f"x{L2}a{R2}y and x{R2}a{L2}y"
    assert find_actual_string(text, '"a"') == f"{L2}a{R2}"


def test_curly_single_quote_edges():
    # index 0 is an opening context even when the string ends in a letter
    assert preserve_quote_style("'a'", f"{L1}a{R1}", "'ab") == f"{L1}ab"
    # a contraction at the second-to-last position must not read past the end
    assert preserve_quote_style("'a'", f"{L1}a{R1}", "it's") == f"it{R1}s"


def test_preserve_quote_style_needs_only_one_curly_kind():
    assert preserve_quote_style('"a', f"{L2}a", '"b"') == f"{L2}b{R2}"
    assert preserve_quote_style("'a", f"{L1}a", "'b'") == f"{L1}b{R1}"
    # double-only file: single quotes in new_string stay straight
    assert preserve_quote_style('"a"', f"{L2}a{R2}", "'b'") == "'b'"


def test_apply_edit_to_file_edge_cases():
    # old ends with a newline: no extra newline is eaten
    assert apply_edit_to_file("y\n\nz", "y\n", "", False) == "\nz"
    # single replacement of a deleted line leaves later copies alone
    assert apply_edit_to_file("y\ny\nz", "y", "", False) == "y\nz"


def test_missing_file_path_errors_like_the_workspace():
    ctx = ctx_for(FakeWorkspace())
    res = run(build_read_file_tool().call({}, ctx))
    assert res.is_error is True and res.content == "Error reading file: Path must not be empty."
    res = build_write_file_tool().validate_input({}, ctx)
    assert (res.valid, res.message) == (False, "Path must not be empty.")
    res = run(build_write_file_tool().call({}, ctx))
    assert res.content == "Error writing file: Path must not be empty."
    res = build_edit_tool().validate_input({"old_string": "a", "new_string": "b"}, ctx)
    assert (res.valid, res.message) == (False, "Path must not be empty.")
    res = run(build_edit_tool().call({"old_string": "a", "new_string": "b"}, ctx))
    assert res.content == "Error editing file: Path must not be empty."


def test_offset_only_read_is_recorded_as_partial():
    ctx = ctx_for(FakeWorkspace(files={"f.txt": "a\nb\n"}))
    read(ctx, "f.txt", offset=2)
    assert ctx.read_file_state["f.txt"] == FileState(content="a\nb\n", offset=2, limit=None)


def test_edit_defaults_for_missing_strings():
    ws = FakeWorkspace()
    ctx = ctx_for(ws)
    # missing old_string means "" -> creates the missing file
    assert validate(build_edit_tool, {"file_path": "n.py", "new_string": "x"}, ctx).valid is True
    run(build_edit_tool().call({"file_path": "n.py", "new_string": "x"}, ctx))
    assert ws.files["n.py"] == "x"
    # missing new_string means "": identical to a missing old_string
    res = validate(build_edit_tool, {"file_path": "n.py", "old_string": ""}, ctx)
    assert (res.valid, res.error_code) == (False, 1)


def test_edit_exactly_two_matches_needs_replace_all():
    _, ctx = read_ctx({"f.txt": "a a"})
    res = validate(build_edit_tool, edit_args(), ctx)
    assert (res.valid, res.error_code) == (False, 9)
    assert res.message.startswith("Found 2 matches")


def test_edit_call_on_empty_existing_file_keeps_it_empty_when_no_match():
    ws, ctx = read_ctx({"f.txt": ""})
    run(build_edit_tool().call(edit_args(old="x", new="y"), ctx))
    assert ws.files["f.txt"] == ""


def test_edit_exact_curly_match_keeps_new_string_verbatim():
    ws, ctx = read_ctx({"f.txt": f"say {L2}a{R2}"})
    run(build_edit_tool().call(edit_args(old=f"{L2}a{R2}", new='"b"'), ctx))
    assert ws.files["f.txt"] == 'say "b"'


def test_glob_empty_pattern_matches_everything(local_ws, tmp_path):
    make_tree(tmp_path, {"a.py": "", "d/b.txt": ""})
    assert glob(local_ws).content == "a.py\nd/b.txt"


def test_build_grep_args_multiline_keeps_earlier_flags():
    assert fs.build_grep_args({"pattern": "p", "multiline": True}) == [
        "--hidden", *VCS_GLOBS, "--max-columns", "500", "-U", "--multiline-dotall", "-l", "p"]


def test_grep_validation_error_codes_are_zero():
    ctx = ctx_for(FakeWorkspace())
    assert validate(build_grep_tool, {"pattern": "x", "offset": -1}, ctx).error_code == 0
    assert validate(build_grep_tool, {"pattern": "x", "output_mode": "bad"}, ctx).error_code == 0


def test_grep_count_parsing_edges(monkeypatch):
    monkeypatch.setattr(fs, "rip_grep", lambda ws, a, t: ["a:b:3", ":4", "c:-2"])
    res = grep(FakeWorkspace(), pattern="p", output_mode="count")
    assert res.content.endswith("Found 1 total occurrence across 2 files.")


def test_grep_count_pagination_reports_offset(monkeypatch):
    monkeypatch.setattr(fs, "rip_grep", lambda ws, a, t: ["a:1", "b:1", "c:1"])
    res = grep(FakeWorkspace(), pattern="p", output_mode="count", offset=1)
    assert res.content == "b:1\nc:1\n\nFound 2 total occurrences across 2 files. with pagination = offset: 1"


@pytest.mark.parametrize("builder, filename", [
    (build_read_file_tool, "read_file.md"),
    (build_write_file_tool, "write_file.md"),
    (build_edit_tool, "edit.md"),
    (build_glob_tool, "glob.md"),
    (build_grep_tool, "grep.md"),
])
def test_tool_prompt_is_its_markdown_body(builder, filename):
    body = get_prompt(filename, "")
    assert body and builder().prompt == body
