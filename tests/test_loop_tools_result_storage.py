"""
Oversized tool-result governance: `app/loop/tools/result_storage.py`.

The numbers here are upstream's and the tests exist to keep them so. The behavioural
claims — head-only preview, clamp not default, infinity opt-out, fail-open, create-only —
are each pinned separately, because each one is a different way to get this wrong.
"""

from __future__ import annotations

import math

import pytest

from app.loop.tools.result_storage import (
    DEFAULT_MAX_RESULT_SIZE_CHARS,
    MAX_TOOL_RESULT_BYTES,
    PERSISTED_OUTPUT_CLOSING_TAG,
    PERSISTED_OUTPUT_TAG,
    PREVIEW_SIZE_BYTES,
    TOOL_RESULTS_SUBDIR,
    build_large_tool_result_message,
    config_home,
    format_file_size,
    generate_preview,
    get_persistence_threshold,
    maybe_persist_large_result,
    persist_tool_result,
    sanitize_path,
    tool_results_dir,
)


@pytest.fixture(autouse=True)
def isolated_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


def test_constants_match_upstream():
    assert DEFAULT_MAX_RESULT_SIZE_CHARS == 50_000
    assert MAX_TOOL_RESULT_BYTES == 400_000
    assert PREVIEW_SIZE_BYTES == 2000
    assert (PERSISTED_OUTPUT_TAG, PERSISTED_OUTPUT_CLOSING_TAG) == (
        "<persisted-output>", "</persisted-output>")
    assert TOOL_RESULTS_SUBDIR == "tool-results"


# ── threshold ───────────────────────────────────────────────────────────────

def test_the_default_is_a_clamp_not_a_fallback():
    """A tool asking for more than the default still gets the default."""
    assert get_persistence_threshold(100_000) == DEFAULT_MAX_RESULT_SIZE_CHARS
    assert get_persistence_threshold(30_000) == 30_000
    assert get_persistence_threshold(10_000) == 10_000


def test_infinity_is_a_hard_opt_out():
    assert get_persistence_threshold(math.inf) == math.inf


# ── formatting ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("size,expected", [
    (0, "0 bytes"), (1, "1 bytes"), (1023, "1023 bytes"),
    (1024, "1KB"), (1536, "1.5KB"), (1024 * 1024, "1MB"),
    (int(2.5 * 1024 * 1024), "2.5MB"), (3 * 1024 ** 3, "3GB"),
])
def test_file_sizes_render_the_way_upstream_renders_them(size, expected):
    assert format_file_size(size) == expected


# ── preview ─────────────────────────────────────────────────────────────────

def test_short_content_is_its_own_preview():
    assert generate_preview("hello") == ("hello", False)


def test_content_at_exactly_the_limit_is_not_truncated():
    content = "x" * PREVIEW_SIZE_BYTES
    assert generate_preview(content) == (content, False)


def test_a_long_line_is_cut_hard_rather_than_collapsing_the_preview():
    """The newline snap applies only past the halfway mark, so this cuts at the limit."""
    preview, has_more = generate_preview("y" * 5000)
    assert (len(preview), has_more) == (PREVIEW_SIZE_BYTES, True)


def test_a_newline_past_the_halfway_mark_is_the_cut_point():
    content = "line\n" * 600
    preview, has_more = generate_preview(content)
    assert has_more is True
    assert preview.endswith("line")
    assert len(preview) < PREVIEW_SIZE_BYTES


def test_an_early_newline_is_ignored_in_favour_of_the_hard_cut():
    content = "a\n" + "b" * 5000
    preview, _ = generate_preview(content)
    assert len(preview) == PREVIEW_SIZE_BYTES


def test_the_preview_is_head_only_and_never_carries_a_tail():
    content = "HEAD" + ("m" * 5000) + "TAIL"
    preview, _ = generate_preview(content)
    assert preview.startswith("HEAD")
    assert "TAIL" not in preview


# ── the replacement message ─────────────────────────────────────────────────

def test_the_replacement_message_has_upstreams_exact_shape():
    message = build_large_tool_result_message(312_400, "/tmp/x/toolu_1.txt", "first bit", True)
    assert message == (
        "<persisted-output>\n"
        "Output too large (305.1KB). Full output saved to: /tmp/x/toolu_1.txt\n\n"
        "Preview (first 2KB):\n"
        "first bit\n...\n"
        "</persisted-output>"
    )


def test_a_complete_preview_ends_without_the_ellipsis():
    message = build_large_tool_result_message(10, "/tmp/f.txt", "all of it", False)
    assert message.endswith("all of it\n</persisted-output>")


# ── paths ───────────────────────────────────────────────────────────────────

def test_the_config_home_is_overridable_and_defaults_under_the_home_directory(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_CONFIG_DIR", raising=False)
    assert config_home().name == ".tddagents"
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", "/somewhere/else")
    assert str(config_home()) == "/somewhere/else"


def test_paths_are_sanitized_to_alphanumerics():
    assert sanitize_path("/home/u/my proj") == "-home-u-my-proj"


def test_a_very_long_path_is_truncated_with_a_stable_hash():
    long_name = "a" * 500
    first, second = sanitize_path(long_name), sanitize_path(long_name)
    assert first == second
    assert len(first) == 209
    assert sanitize_path("b" * 500) != first


def test_the_directory_layout_mirrors_upstream():
    directory = tool_results_dir("loop-123", "/home/u/proj")
    assert directory.name == TOOL_RESULTS_SUBDIR
    assert directory.parent.name == "loop-123"
    assert directory.parent.parent.name == "-home-u-proj"
    assert directory.parent.parent.parent.name == "projects"


# ── persistence ─────────────────────────────────────────────────────────────

def test_content_is_written_where_the_preview_says_it_is():
    path = persist_tool_result("the whole output", "toolu_1", "run-1", "/proj")
    assert path is not None and path.read_text() == "the whole output"
    assert path.name == "toolu_1.txt"


def test_writing_the_same_id_twice_keeps_the_first_file():
    """Create-only: the id is unique and its content deterministic, so a replay reuses it."""
    first = persist_tool_result("original", "toolu_1", "run-1", "/proj")
    second = persist_tool_result("different", "toolu_1", "run-1", "/proj")
    assert first == second
    assert first is not None and first.read_text() == "original"


def test_an_unwritable_store_reports_failure_rather_than_raising(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("pathlib.Path.mkdir", refuse)
    assert persist_tool_result("x", "toolu_1", "run-1", "/proj") is None


# ── the dispatcher ──────────────────────────────────────────────────────────

def _persist(content: str, **kwargs: object) -> str:
    defaults: dict[str, object] = {"tool_name": "Bash", "tool_use_id": "toolu_1",
                                   "run_id": "run-1", "working_directory": "/proj"}
    defaults.update(kwargs)
    return maybe_persist_large_result(content, **defaults)  # type: ignore[arg-type]


def test_content_under_the_threshold_is_returned_untouched():
    assert _persist("small output", max_result_size_chars=1000) == "small output"


def test_oversized_content_is_replaced_by_a_preview_naming_a_real_file():
    content = "x" * 3000
    result = _persist(content, max_result_size_chars=1000)
    assert result.startswith(PERSISTED_OUTPUT_TAG)
    path = result.split("Full output saved to: ")[1].split("\n")[0]
    from pathlib import Path

    assert Path(path).read_text() == content


def test_empty_output_is_named_rather_than_left_blank():
    """Upstream's inc-4586: an empty tool result makes some models emit a stop sequence."""
    assert _persist("") == "(Bash completed with no output)"
    assert _persist("   \n  ") == "(Bash completed with no output)"


def test_a_tool_that_opted_out_is_never_persisted():
    content = "x" * 1_000_000
    assert _persist(content, max_result_size_chars=math.inf) == content


def test_a_write_failure_leaves_the_original_content_alone(monkeypatch):
    """Fail-open: a storage problem must not also destroy the output."""
    monkeypatch.setattr("app.loop.tools.result_storage.persist_tool_result", lambda *a, **k: None)
    content = "x" * 3000
    assert _persist(content, max_result_size_chars=1000) == content


def test_the_declared_limit_only_bites_when_it_is_below_the_clamp():
    content = "x" * 60_000
    assert _persist(content, max_result_size_chars=100_000).startswith(PERSISTED_OUTPUT_TAG)
    assert _persist("x" * 40_000, tool_use_id="toolu_2", max_result_size_chars=100_000) == "x" * 40_000


def test_a_well_formed_tool_use_id_is_used_verbatim_as_the_filename():
    from app.loop.tools.result_storage import safe_file_stem

    assert safe_file_stem("toolu_01ABCdef") == "toolu_01ABCdef"
    assert safe_file_stem("call-7.part") == "call-7.part"


@pytest.mark.parametrize("hostile", ["../../etc/passwd", "a/b", "..", "", "...", "~/x", "a\\b"])
def test_an_id_that_could_leave_the_directory_cannot(hostile):
    """No separator survives and no name starts with a dot, so the result stays one file."""
    from app.loop.tools.result_storage import safe_file_stem

    stem = safe_file_stem(hostile)
    assert stem
    assert "/" not in stem and "\\" not in stem
    assert not stem.startswith(".")


def test_an_empty_or_dotted_id_still_produces_a_usable_name():
    from app.loop.tools.result_storage import safe_file_stem

    assert safe_file_stem("") == "result"
    assert safe_file_stem("...") == "result"


def test_a_hostile_id_still_writes_inside_the_store():
    path = persist_tool_result("x", "../../escape", "run-1", "/proj")
    assert path is not None
    assert path.parent == tool_results_dir("run-1", "/proj")


# ── boundaries ──────────────────────────────────────────────────────────────

def test_a_newline_exactly_at_the_halfway_mark_is_not_the_cut_point():
    """The snap needs `> half`, not `>= half`, or a near-useless preview would win."""
    content = ("a" * (PREVIEW_SIZE_BYTES // 2)) + "\n" + ("b" * PREVIEW_SIZE_BYTES)
    preview, has_more = generate_preview(content)
    assert (len(preview), has_more) == (PREVIEW_SIZE_BYTES, True)


def test_a_newline_one_past_the_halfway_mark_is_the_cut_point():
    content = ("a" * (PREVIEW_SIZE_BYTES // 2 + 1)) + "\n" + ("b" * PREVIEW_SIZE_BYTES)
    preview, has_more = generate_preview(content)
    assert (len(preview), has_more) == (PREVIEW_SIZE_BYTES // 2 + 1, True)


def test_content_exactly_at_the_threshold_is_not_persisted():
    """`size <= threshold` returns it untouched; one character more does not."""
    assert _persist("x" * 1000, max_result_size_chars=1000) == "x" * 1000
    assert _persist("x" * 1001, max_result_size_chars=1000).startswith(PERSISTED_OUTPUT_TAG)


def test_a_persisted_result_whose_preview_is_complete_omits_the_ellipsis():
    result = _persist("y" * 1500, max_result_size_chars=1000)
    assert result.endswith("y\n</persisted-output>")
    assert "\n...\n" not in result


def test_the_file_lands_under_the_run_and_working_directory_it_was_given(tmp_path):
    _persist("x" * 3000, max_result_size_chars=1000, run_id="run-7",
             working_directory="/some/proj", tool_use_id="toolu_9")
    expected = tool_results_dir("run-7", "/some/proj") / "toolu_9.txt"
    assert expected.exists()


def test_a_directory_that_cannot_be_created_reports_failure(monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError("nope")

    monkeypatch.setattr("pathlib.Path.mkdir", refuse)
    assert persist_tool_result("x", "toolu_1", "run-1", "/proj") is None


def test_a_file_that_cannot_be_written_reports_failure(monkeypatch):
    import builtins

    real_open = builtins.open

    def refuse(path, mode="r", *args, **kwargs):
        if mode == "x":
            raise PermissionError("nope")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", refuse)
    assert persist_tool_result("x", "toolu_1", "run-1", "/proj") is None
