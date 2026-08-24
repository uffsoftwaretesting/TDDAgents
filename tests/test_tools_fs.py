"""
The nine filesystem tools.

Read/write tools run against the in-memory FakeWorkspace where that is enough, and against
a real LocalWorkspace in tmp_path wherever the behavior depends on a real filesystem —
Grep and Glob shell out to `grep` and `find`, so a canned `execute` would only prove they
build a command string.

Everything goes through `execute_tool`, never `tool.call` directly, so the gates and the
result governance are exercised the way the runtime will exercise them.
"""

from __future__ import annotations

import pytest

from app.tools.base import Capability, execute_tool
from app.tools.fs import (
    DEFAULT_READ_LINES,
    MAX_LINE_CHARS,
    Delete,
    Edit,
    Glob,
    Grep,
    ListDir,
    Move,
    MultiEdit,
    ReadFile,
    WriteFile,
)


def run(tool, args, ctx):
    return execute_tool(tool, args, ctx)


def seed(ctx, **files):
    for path, content in files.items():
        ctx.workspace.write_file(path.replace("__", "/"), content)


class TestReadFile:
    def test_reads_with_line_numbers(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "first\nsecond"})
        result = run(ReadFile, {"path": "a.py"}, tool_ctx)
        assert result.content == "1\tfirst\n2\tsecond"

    def test_line_numbers_are_right_aligned(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "\n".join(f"line{i}" for i in range(12))})
        lines = run(ReadFile, {"path": "a.py"}, tool_ctx).content.splitlines()
        assert lines[0].startswith(" 1\t")
        assert lines[11].startswith("12\t")

    def test_missing_file_is_an_error_result(self, tool_ctx):
        result = run(ReadFile, {"path": "nope.py"}, tool_ctx)
        assert result.is_error is True
        assert "not found" in result.content

    def test_empty_file_says_so(self, tool_ctx):
        seed(tool_ctx, **{"empty.py": ""})
        assert "empty" in run(ReadFile, {"path": "empty.py"}, tool_ctx).content

    def test_offset_and_limit_page_the_file(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "\n".join(str(i) for i in range(100))})
        result = run(ReadFile, {"path": "a.py", "offset": 10, "limit": 3}, tool_ctx)
        assert "11\t10" in result.content
        assert "13\t12" in result.content
        assert "14\t13" not in result.content

    def test_a_long_file_reports_how_to_continue(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "\n".join(str(i) for i in range(DEFAULT_READ_LINES + 50))})
        result = run(ReadFile, {"path": "a.py"}, tool_ctx)
        assert "50 more line(s)" in result.content
        assert f"offset={DEFAULT_READ_LINES}" in result.content

    def test_a_short_file_has_no_continuation_notice(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "one\ntwo"})
        assert "more line(s)" not in run(ReadFile, {"path": "a.py"}, tool_ctx).content

    def test_offset_past_the_end(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "one\ntwo"})
        result = run(ReadFile, {"path": "a.py", "offset": 99}, tool_ctx)
        assert "past the end" in result.content

    def test_a_very_long_line_is_cut_with_a_marker(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "x" * (MAX_LINE_CHARS + 500)})
        result = run(ReadFile, {"path": "a.py"}, tool_ctx)
        assert "line cut at" in result.content
        assert len(result.content) < MAX_LINE_CHARS + 200

    def test_is_read_only_and_parallelizable(self):
        args = ReadFile.args_schema(path="a.py")
        assert ReadFile.is_read_only(args) is True
        assert ReadFile.is_concurrency_safe(args) is True
        assert ReadFile.required_capability(args) is Capability.READ

    def test_output_is_never_persisted(self, tool_ctx, fake_sandbox):
        """Persisting Read would make the preview point at a file containing the preview."""
        seed(tool_ctx, **{"big.py": "\n".join("y" * 200 for _ in range(2000))})
        result = run(ReadFile, {"path": "big.py"}, tool_ctx)
        assert result.truncated is False
        assert result.persisted_path is None
        assert not any(p.startswith(".tddagents") for p in fake_sandbox.files)


class TestListDir:
    def test_lists_files_with_a_type_marker(self, local_tool_ctx):
        seed(local_tool_ctx, **{"a.py": "x", "b.py": "yy"})
        content = run(ListDir, {"path": "."}, local_tool_ctx).content
        assert "- a.py" in content
        assert "- b.py" in content

    def test_reports_sizes_for_files(self, local_tool_ctx):
        seed(local_tool_ctx, **{"a.py": "12345"})
        assert "(5 bytes)" in run(ListDir, {"path": "."}, local_tool_ctx).content

    def test_directories_are_marked_and_unsized(self, local_tool_ctx):
        seed(local_tool_ctx, **{"pkg__mod.py": "x"})
        content = run(ListDir, {"path": "."}, local_tool_ctx).content
        assert "d pkg" in content
        assert "d pkg  (" not in content

    def test_empty_directory(self, local_tool_ctx):
        assert "empty" in run(ListDir, {"path": "."}, local_tool_ctx).content

    def test_missing_directory_is_an_error(self, local_tool_ctx):
        result = run(ListDir, {"path": "nope"}, local_tool_ctx)
        assert result.is_error is True

    def test_depth_controls_descent(self, local_tool_ctx):
        seed(local_tool_ctx, **{"pkg__deep__x.py": "x"})
        shallow = run(ListDir, {"path": ".", "depth": 1}, local_tool_ctx).content
        deep = run(ListDir, {"path": ".", "depth": 3}, local_tool_ctx).content
        assert "pkg/deep/x.py" not in shallow
        assert "pkg/deep/x.py" in deep


class TestGlob:
    def test_finds_by_extension(self, local_tool_ctx):
        seed(local_tool_ctx, **{"a.py": "x", "b.txt": "y"})
        content = run(Glob, {"pattern": "*.py"}, local_tool_ctx).content
        assert "a.py" in content
        assert "b.txt" not in content

    def test_finds_nested_files(self, local_tool_ctx):
        seed(local_tool_ctx, **{"pkg__mod.py": "x"})
        assert "pkg/mod.py" in run(Glob, {"pattern": "*.py"}, local_tool_ctx).content

    def test_a_pattern_with_a_slash_matches_the_path(self, local_tool_ctx):
        seed(local_tool_ctx, **{"tests__test_a.py": "x", "src__a.py": "y"})
        content = run(Glob, {"pattern": "*/tests/*.py"}, local_tool_ctx).content
        assert "tests/test_a.py" in content
        assert "src/a.py" not in content

    def test_no_matches_says_so(self, local_tool_ctx):
        assert "No files match" in run(Glob, {"pattern": "*.rs"}, local_tool_ctx).content

    def test_is_read_only_and_parallelizable(self):
        args = Glob.args_schema(pattern="*.py")
        assert Glob.is_read_only(args) is True
        assert Glob.is_concurrency_safe(args) is True


class TestGrep:
    def test_finds_matching_lines_with_paths_and_numbers(self, local_tool_ctx):
        seed(local_tool_ctx, **{"a.py": "import os\ndef f():\n    pass"})
        content = run(Grep, {"pattern": "def f"}, local_tool_ctx).content
        assert "a.py:2:" in content
        assert "def f():" in content

    def test_regular_expressions_work(self, local_tool_ctx):
        seed(local_tool_ctx, **{"a.py": "alpha\nbeta\ngamma"})
        content = run(Grep, {"pattern": "^(alpha|gamma)$"}, local_tool_ctx).content
        assert "alpha" in content
        assert "gamma" in content
        assert "beta" not in content

    def test_case_sensitive_by_default(self, local_tool_ctx):
        seed(local_tool_ctx, **{"a.py": "Hello"})
        assert "No matches" in run(Grep, {"pattern": "hello"}, local_tool_ctx).content

    def test_case_insensitive_flag(self, local_tool_ctx):
        seed(local_tool_ctx, **{"a.py": "Hello"})
        result = run(Grep, {"pattern": "hello", "case_insensitive": True}, local_tool_ctx)
        assert "Hello" in result.content

    def test_glob_filter_restricts_the_files_searched(self, local_tool_ctx):
        seed(local_tool_ctx, **{"a.py": "needle", "b.txt": "needle"})
        content = run(Grep, {"pattern": "needle", "glob": "*.py"}, local_tool_ctx).content
        assert "a.py" in content
        assert "b.txt" not in content

    def test_no_matches_says_so(self, local_tool_ctx):
        seed(local_tool_ctx, **{"a.py": "nothing here"})
        assert "No matches" in run(Grep, {"pattern": "absent"}, local_tool_ctx).content

    def test_a_pattern_with_shell_metacharacters_is_quoted_safely(self, local_tool_ctx):
        """The pattern is agent-authored; it must never reach the shell unquoted."""
        seed(local_tool_ctx, **{"a.py": "value; rm -rf /"})
        result = run(Grep, {"pattern": "value; rm"}, local_tool_ctx)
        assert result.is_error is False
        assert "a.py" in result.content

    def test_result_limit_is_the_tightest_in_the_roster(self):
        from app.tools.base import GREP_MAX_RESULT_CHARS

        assert Grep.max_result_chars == GREP_MAX_RESULT_CHARS


class TestWriteFile:
    def test_creates_a_file(self, tool_ctx):
        result = run(WriteFile, {"path": "new.py", "content": "x = 1"}, tool_ctx)
        assert "Created new.py" in result.content
        assert tool_ctx.workspace.read_file("new.py") == "x = 1"

    def test_overwrites_an_existing_file(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "old"})
        result = run(WriteFile, {"path": "a.py", "content": "new"}, tool_ctx)
        assert "Overwrote a.py" in result.content
        assert tool_ctx.workspace.read_file("a.py") == "new"

    def test_creates_parent_directories(self, local_tool_ctx):
        run(WriteFile, {"path": "deep/nested/a.py", "content": "x"}, local_tool_ctx)
        assert local_tool_ctx.workspace.read_file("deep/nested/a.py") == "x"

    def test_reports_line_and_character_counts(self, tool_ctx):
        result = run(WriteFile, {"path": "a.py", "content": "one\ntwo"}, tool_ctx)
        assert "2 line(s)" in result.content
        assert "7 chars" in result.content

    def test_needs_write_capability(self, make_tool_ctx):
        ctx = make_tool_ctx(permission_mode="read_only")
        result = run(WriteFile, {"path": "a.py", "content": "x"}, ctx)
        assert result.is_error is True
        assert "read_only" in result.content

    def test_is_marked_destructive(self):
        args = WriteFile.args_schema(path="a.py", content="x")
        assert WriteFile.is_destructive(args) is True
        assert WriteFile.is_read_only(args) is False


class TestEdit:
    def test_replaces_unique_text(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "x = 1\ny = 2"})
        run(Edit, {"path": "a.py", "old_string": "x = 1", "new_string": "x = 99"}, tool_ctx)
        assert tool_ctx.workspace.read_file("a.py") == "x = 99\ny = 2"

    def test_missing_file(self, tool_ctx):
        result = run(Edit, {"path": "n.py", "old_string": "a", "new_string": "b"}, tool_ctx)
        assert result.is_error is True
        assert "WriteFile" in result.content

    def test_text_not_found(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "x = 1"})
        result = run(Edit, {"path": "a.py", "old_string": "absent", "new_string": "b"}, tool_ctx)
        assert result.is_error is True
        assert "not found" in result.content

    def test_ambiguous_match_fails_loudly(self, tool_ctx):
        """Changing an arbitrary one of three would be worse than refusing."""
        seed(tool_ctx, **{"a.py": "dup\ndup\ndup"})
        result = run(Edit, {"path": "a.py", "old_string": "dup", "new_string": "x"}, tool_ctx)
        assert result.is_error is True
        assert "3 times" in result.content
        assert tool_ctx.workspace.read_file("a.py") == "dup\ndup\ndup"

    def test_replace_all_resolves_ambiguity(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "dup\ndup"})
        run(
            Edit,
            {"path": "a.py", "old_string": "dup", "new_string": "x", "replace_all": True},
            tool_ctx,
        )
        assert tool_ctx.workspace.read_file("a.py") == "x\nx"

    def test_identical_strings_are_rejected(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "same"})
        result = run(Edit, {"path": "a.py", "old_string": "s", "new_string": "s"}, tool_ctx)
        assert result.is_error is True
        assert "identical" in result.content

    def test_a_failed_edit_writes_nothing(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "original"})
        run(Edit, {"path": "a.py", "old_string": "absent", "new_string": "x"}, tool_ctx)
        assert tool_ctx.workspace.read_file("a.py") == "original"

    def test_multiline_replacement(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "def f():\n    pass\n"})
        run(
            Edit,
            {"path": "a.py", "old_string": "def f():\n    pass", "new_string": "def f():\n    return 1"},
            tool_ctx,
        )
        assert "return 1" in tool_ctx.workspace.read_file("a.py")


class TestMultiEdit:
    def _edits(self, *pairs):
        return [{"old_string": o, "new_string": n} for o, n in pairs]

    def test_applies_edits_in_order(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "one two three"})
        run(
            MultiEdit,
            {"path": "a.py", "edits": self._edits(("one", "1"), ("two", "2"))},
            tool_ctx,
        )
        assert tool_ctx.workspace.read_file("a.py") == "1 2 three"

    def test_a_later_edit_sees_an_earlier_one(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "a"})
        run(MultiEdit, {"path": "a.py", "edits": self._edits(("a", "b"), ("b", "c"))}, tool_ctx)
        assert tool_ctx.workspace.read_file("a.py") == "c"

    def test_all_or_nothing_on_failure(self, tool_ctx):
        """A half-applied batch leaves a file in a state the agent never asked for."""
        seed(tool_ctx, **{"a.py": "one two"})
        result = run(
            MultiEdit,
            {"path": "a.py", "edits": self._edits(("one", "1"), ("absent", "x"))},
            tool_ctx,
        )
        assert result.is_error is True
        assert tool_ctx.workspace.read_file("a.py") == "one two"

    def test_the_error_names_which_edit_failed(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "one two"})
        result = run(
            MultiEdit,
            {"path": "a.py", "edits": self._edits(("one", "1"), ("absent", "x"))},
            tool_ctx,
        )
        assert "Edit 2 of 2" in result.content

    def test_empty_edit_list_is_rejected(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "x"})
        result = run(MultiEdit, {"path": "a.py", "edits": []}, tool_ctx)
        assert result.is_error is True
        assert "must not be empty" in result.content

    def test_missing_file(self, tool_ctx):
        result = run(MultiEdit, {"path": "n.py", "edits": self._edits(("a", "b"))}, tool_ctx)
        assert result.is_error is True

    def test_reports_how_many_edits_landed(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "one two"})
        result = run(
            MultiEdit, {"path": "a.py", "edits": self._edits(("one", "1"), ("two", "2"))}, tool_ctx
        )
        assert "2 edit(s)" in result.content


class TestDeleteAndMove:
    def test_delete_removes_the_file(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "x"})
        run(Delete, {"path": "a.py"}, tool_ctx)
        assert tool_ctx.workspace.exists("a.py") is False

    def test_delete_missing_file_is_an_error(self, tool_ctx):
        result = run(Delete, {"path": "nope.py"}, tool_ctx)
        assert result.is_error is True

    def test_move_relocates_the_file(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "content"})
        run(Move, {"old": "a.py", "new": "b.py"}, tool_ctx)
        assert tool_ctx.workspace.exists("a.py") is False
        assert tool_ctx.workspace.read_file("b.py") == "content"

    def test_move_missing_file_is_an_error(self, tool_ctx):
        result = run(Move, {"old": "nope.py", "new": "b.py"}, tool_ctx)
        assert result.is_error is True

    def test_move_creates_destination_directories(self, local_tool_ctx):
        seed(local_tool_ctx, **{"a.py": "x"})
        run(Move, {"old": "a.py", "new": "deep/b.py"}, local_tool_ctx)
        assert local_tool_ctx.workspace.read_file("deep/b.py") == "x"

    @pytest.mark.parametrize("tool,args", [
        (Delete, {"path": "a.py"}),
        (Move, {"old": "a.py", "new": "b.py"}),
    ])
    def test_both_are_destructive_and_need_write(self, tool, args):
        parsed = tool.args_schema(**args)
        assert tool.is_destructive(parsed) is True
        assert tool.required_capability(parsed) is Capability.WRITE


class TestWriteToolsTriggerTheSyncCheckpoint:
    """The lost-files fix only works if write tools are actually classified as writes."""

    class _Engine:
        def __init__(self):
            self.calls = 0

        def reconcile_ledger(self, ledger):
            self.calls += 1
            return dict(ledger), object()

    @pytest.mark.parametrize("tool,args", [
        (WriteFile, {"path": "a.py", "content": "x"}),
        (Edit, {"path": "a.py", "old_string": "x", "new_string": "y"}),
        (Delete, {"path": "a.py"}),
        (Move, {"old": "a.py", "new": "b.py"}),
    ])
    def test_write_tools_reconcile(self, make_tool_ctx, fake_sandbox, tool, args):
        engine = self._Engine()
        ctx = make_tool_ctx(sync_engine=engine)
        ctx.workspace.write_file("a.py", "x")
        run(tool, args, ctx)
        assert engine.calls == 1

    @pytest.mark.parametrize("tool,args", [
        (ReadFile, {"path": "a.py"}),
        (ListDir, {"path": "."}),
    ])
    def test_read_tools_do_not_reconcile(self, make_tool_ctx, tool, args):
        engine = self._Engine()
        ctx = make_tool_ctx(sync_engine=engine)
        ctx.workspace.write_file("a.py", "x")
        run(tool, args, ctx)
        assert engine.calls == 0


class TestToolNameIsStampedCentrally:
    """
    A tool no longer writes its own name into its results — `execute_tool` stamps it. This
    is what makes it impossible for a tool to mislabel its own output, and it removed 56
    hand-copied literals.
    """

    @pytest.mark.parametrize("tool,args,expected", [
        (ReadFile, {"path": "a.py"}, "ReadFile"),
        (ListDir, {"path": "."}, "ListDir"),
        (Grep, {"pattern": "x"}, "Grep"),
        (Glob, {"pattern": "*.py"}, "Glob"),
        (WriteFile, {"path": "b.py", "content": "y"}, "WriteFile"),
        (Edit, {"path": "a.py", "old_string": "x", "new_string": "z"}, "Edit"),
        (MultiEdit, {"path": "a.py", "edits": [{"old_string": "x", "new_string": "z"}]}, "MultiEdit"),
        (Move, {"old": "a.py", "new": "c.py"}, "Move"),
        (Delete, {"path": "a.py"}, "Delete"),
    ])
    def test_success_results_are_named(self, local_tool_ctx, tool, args, expected):
        seed(local_tool_ctx, **{"a.py": "x"})
        assert run(tool, args, local_tool_ctx).tool_name == expected

    @pytest.mark.parametrize("tool,args,expected", [
        (ReadFile, {"path": "absent.py"}, "ReadFile"),
        (ListDir, {"path": "absent"}, "ListDir"),
        (Edit, {"path": "absent.py", "old_string": "x", "new_string": "z"}, "Edit"),
        (MultiEdit, {"path": "absent.py", "edits": [{"old_string": "x", "new_string": "z"}]}, "MultiEdit"),
        (Delete, {"path": "absent.py"}, "Delete"),
        (Move, {"old": "absent.py", "new": "c.py"}, "Move"),
    ])
    def test_error_results_are_named_too(self, local_tool_ctx, tool, args, expected):
        result = run(tool, args, local_tool_ctx)
        assert result.is_error is True
        assert result.tool_name == expected


class TestReadFileBoundaries:
    """Off-by-one territory: the exact points where paging and clipping switch behavior."""

    def test_a_line_exactly_at_the_limit_is_not_clipped(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "x" * MAX_LINE_CHARS})
        assert "line cut at" not in run(ReadFile, {"path": "a.py"}, tool_ctx).content

    def test_one_character_over_the_limit_is_clipped(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "x" * (MAX_LINE_CHARS + 1)})
        assert "line cut at" in run(ReadFile, {"path": "a.py"}, tool_ctx).content

    def test_a_limit_of_zero_returns_no_window(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "one\ntwo"})
        result = run(ReadFile, {"path": "a.py", "limit": 0}, tool_ctx)
        assert "past the end" in result.content

    def test_exactly_one_remaining_line_is_reported(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "one\ntwo\nthree"})
        result = run(ReadFile, {"path": "a.py", "limit": 2}, tool_ctx)
        assert "1 more line(s)" in result.content
        assert "offset=2" in result.content

    def test_reading_exactly_to_the_end_reports_nothing_remaining(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "one\ntwo"})
        result = run(ReadFile, {"path": "a.py", "limit": 2}, tool_ctx)
        assert "more line(s)" not in result.content

    def test_a_negative_offset_is_clamped_to_the_start(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "one\ntwo"})
        assert "1\tone" in run(ReadFile, {"path": "a.py", "offset": -5}, tool_ctx).content

    def test_a_negative_limit_returns_no_window(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "one\ntwo"})
        assert "past the end" in run(ReadFile, {"path": "a.py", "limit": -1}, tool_ctx).content

    def test_a_file_of_exactly_one_line(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "only"})
        assert run(ReadFile, {"path": "a.py"}, tool_ctx).content == "1\tonly"

    def test_a_whitespace_only_file_is_not_treated_as_empty(self, tool_ctx):
        seed(tool_ctx, **{"a.py": "   "})
        result = run(ReadFile, {"path": "a.py"}, tool_ctx)
        assert "exists but is empty" not in result.content
        assert "1\t   " == result.content
