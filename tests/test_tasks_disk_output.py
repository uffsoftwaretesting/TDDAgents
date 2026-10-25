"""Phase 8: a task's output file (`utils/task/diskOutput.ts`, `utils/task/outputFormatting.ts`)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.loop.context import AppStateStore, tool_context_for
from app.loop.storage.session_storage import TranscriptStore
from app.loop.tasks.disk_output import (
    TASK_MAX_OUTPUT_DEFAULT,
    TASK_MAX_OUTPUT_UPPER_LIMIT,
    format_task_output,
    get_max_task_output_length,
    get_task_output,
    get_task_output_dir,
    get_task_output_path,
    init_task_output_as_symlink,
)


def test_the_output_dir_sits_in_the_session_directory_beside_the_transcript(tmp_path):
    store = TranscriptStore("loop-1", tmp_path, version="v", git_branch=None)
    ctx = tool_context_for(AppStateStore(), transcript_store=store, run_id="loop-1")
    assert get_task_output_dir(ctx) == store.path.parent / "loop-1" / "tasks"
    assert get_task_output_path(ctx, "a1") == store.path.parent / "loop-1" / "tasks" / "a1.output"


def test_without_a_transcript_the_dir_comes_from_the_workspace_and_run(tmp_path, monkeypatch):
    from app.loop.storage.paths import get_project_dir

    ctx = tool_context_for(AppStateStore(), workspace=SimpleNamespace(root=str(tmp_path)), run_id="loop-2")
    assert get_task_output_dir(ctx) == get_project_dir(str(tmp_path)) / "loop-2" / "tasks"


def test_the_output_is_a_symlink_to_the_target(tmp_path):
    target = tmp_path / "agent-a1.jsonl"
    target.write_text("line\n")
    out = tmp_path / "tasks" / "a1.output"
    assert init_task_output_as_symlink(out, target) == out
    assert out.is_symlink() and out.read_text() == "line\n"
    # Re-initialising (a resumed agent) replaces the link.
    other = tmp_path / "other.jsonl"
    other.write_text("other\n")
    init_task_output_as_symlink(out, other)
    assert out.read_text() == "other\n"


def test_a_failed_symlink_falls_back_to_an_empty_file(tmp_path, monkeypatch):
    import os

    def boom(*args, **kwargs):
        raise OSError("no symlinks here")

    monkeypatch.setattr(os, "symlink", boom)
    out = tmp_path / "tasks" / "a1.output"
    assert init_task_output_as_symlink(out, tmp_path / "t.jsonl") == out
    assert out.is_file() and not out.is_symlink() and out.read_text() == ""


def test_get_task_output_tails_and_says_what_it_omitted(tmp_path):
    out = tmp_path / "a1.output"
    assert get_task_output(out) == ""
    out.write_text("x" * 3000 + "tail")
    assert get_task_output(out) == "x" * 3000 + "tail"
    assert get_task_output(out, max_bytes=4) == "[3KB of earlier output omitted]\ntail"
    out.write_text("")
    assert get_task_output(out) == ""


def test_the_max_output_length_is_a_bounded_env(monkeypatch):
    monkeypatch.delenv("TASK_MAX_OUTPUT_LENGTH", raising=False)
    assert get_max_task_output_length() == TASK_MAX_OUTPUT_DEFAULT == 32_000
    monkeypatch.setenv("TASK_MAX_OUTPUT_LENGTH", "1000")
    assert get_max_task_output_length() == 1000
    monkeypatch.setenv("TASK_MAX_OUTPUT_LENGTH", "999999")
    assert get_max_task_output_length() == TASK_MAX_OUTPUT_UPPER_LIMIT == 160_000
    for bad in ("0", "-3", "abc"):
        monkeypatch.setenv("TASK_MAX_OUTPUT_LENGTH", bad)
        assert get_max_task_output_length() == 32_000


@pytest.mark.parametrize("length", [10, 50])
def test_format_keeps_short_output(length, monkeypatch):
    monkeypatch.setenv("TASK_MAX_OUTPUT_LENGTH", "50")
    assert format_task_output("y" * length, "/o/a1.output") == ("y" * length, False)


def test_format_truncates_from_the_front_with_a_header(monkeypatch):
    monkeypatch.setenv("TASK_MAX_OUTPUT_LENGTH", "60")
    content, truncated = format_task_output("0123456789" * 10, "/o/a1.output")
    header = "[Truncated. Full output: /o/a1.output]\n\n"
    assert truncated is True
    assert content.startswith(header) and len(content) == 60
    assert content.endswith("0123456789")


# ── mutation-gate pins (Phase 8) ────────────────────────────────────────────

def test_a_bare_context_falls_back_to_the_cwd_and_its_run(tmp_path, monkeypatch):
    from app.loop.storage.paths import get_project_dir

    monkeypatch.chdir(tmp_path)
    expected = get_project_dir(str(tmp_path)) / "r9" / "tasks"
    assert get_task_output_dir(SimpleNamespace(run_id="r9")) == expected
    assert get_task_output_dir(SimpleNamespace(run_id="r9", workspace=object())) == expected


def test_relinking_replaces_a_dangling_link(tmp_path):
    out = tmp_path / "tasks" / "a1.output"
    out.parent.mkdir()
    out.symlink_to(tmp_path / "gone.jsonl")
    target = tmp_path / "agent.jsonl"
    target.write_text("now\n")
    init_task_output_as_symlink(out, target)
    assert out.resolve() == target.resolve() and out.read_text() == "now\n"


def test_get_task_output_bounds_exactly_and_rounds_the_omitted_kb(tmp_path):
    out = tmp_path / "a1.output"
    out.write_bytes(b"abcd")
    assert get_task_output(out, max_bytes=4) == "abcd"
    out.write_bytes(b"x" * 1536 + b"tail")
    assert get_task_output(out, max_bytes=4) == "[2KB of earlier output omitted]\ntail"
    out.write_bytes(b"x" * 2048 + b"y" * 1024)
    assert get_task_output(out, max_bytes=1024) == "[2KB of earlier output omitted]\n" + "y" * 1024


def test_invalid_utf8_is_replaced_not_raised(tmp_path):
    out = tmp_path / "a1.output"
    out.write_bytes(b"ok\xff")
    assert get_task_output(out) == "ok�"
    out.write_bytes(b"x" * 10 + b"\xfftail")
    assert get_task_output(out, max_bytes=5) == "[0KB of earlier output omitted]\n�tail"


def test_a_max_output_length_of_one_is_honoured(monkeypatch):
    monkeypatch.setenv("TASK_MAX_OUTPUT_LENGTH", "1")
    assert get_max_task_output_length() == 1
