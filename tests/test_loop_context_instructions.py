"""
Tests for Part E3: Instruction-file loading (app/loop/context/instructions.py).
"""

from pathlib import Path

from app.loop.context.instructions import (
    load_instruction_file,
    strip_html_comments,
)


def test_strip_html_comments_simple() -> None:
    text = "Hello <!-- comment -->World"
    assert strip_html_comments(text) == "Hello World"
    assert strip_html_comments("") == ""


def test_strip_html_comments_multiline() -> None:
    text = "Start\n<!--\nmultiline\ncomment\n-->\nEnd"
    assert strip_html_comments(text) == "Start\n\nEnd"


def test_strip_html_comments_nested_or_iterative() -> None:
    # Iterative stripping until stable
    text = "Before <!-- outer <!-- inner --> outer --> After"
    cleaned = strip_html_comments(text)
    assert "<!--" not in cleaned
    assert "-->" not in cleaned


def test_load_instruction_file_not_found(tmp_path: Path) -> None:
    res = load_instruction_file(tmp_path / "NONEXISTENT.md")
    assert res is None


def test_load_instruction_file_basic(tmp_path: Path) -> None:
    file_path = tmp_path / "CLAUDE.md"
    file_path.write_text("# Project Guidelines\n<!-- secret note -->\nFollow TDD.", encoding="utf-8")

    res = load_instruction_file(file_path, strip_comments=True)
    assert res is not None
    assert res.path == str(file_path)
    assert "Follow TDD." in res.content
    assert "secret note" not in res.content
    assert res.truncated is False
    assert res.original_bytes == len(file_path.read_bytes())

    # Default invocation (strip_comments=True, max_bytes=50_000)
    res_default = load_instruction_file(file_path)
    assert res_default is not None
    assert res_default.truncated is False
    assert "secret note" not in res_default.content
    assert res_default.original_bytes == len(file_path.read_bytes())

    # strip_comments=False preserves comments
    res_with_comments = load_instruction_file(file_path, strip_comments=False)
    assert res_with_comments is not None
    assert "secret note" in res_with_comments.content

    # Exact boundary (original_size == max_bytes) -> truncated is False
    raw_size = len(file_path.read_bytes())
    res_exact = load_instruction_file(file_path, max_bytes=raw_size)
    assert res_exact is not None
    assert res_exact.truncated is False
    assert res_exact.original_bytes == raw_size


def test_load_instruction_file_truncated(tmp_path: Path) -> None:

    file_path = tmp_path / "BIG.md"
    content = "A" * 2000
    file_path.write_text(content, encoding="utf-8")

    res = load_instruction_file(file_path, max_bytes=500)
    assert res is not None
    assert res.truncated is True
    assert res.original_bytes == 2000
    assert len(res.content) <= 600  # 500 bytes + truncation notice
    assert "[Truncated: original file size 2000 bytes exceeded limit 500]" in res.content


def test_load_instruction_file_non_utf8(tmp_path: Path) -> None:
    file_path = tmp_path / "NON_UTF8.md"
    file_path.write_bytes(b"hello \xff\xfe world " * 100)

    # Untruncated decode with replace
    res = load_instruction_file(file_path, max_bytes=50_000)
    assert res is not None
    assert "\ufffd" in res.content

    # Truncated decode with replace
    res_trunc = load_instruction_file(file_path, max_bytes=10)
    assert res_trunc is not None
    assert res_trunc.truncated is True
    assert "[Truncated" in res_trunc.content


def test_load_instruction_file_default_max_bytes_boundary(tmp_path: Path) -> None:
    f50001 = tmp_path / "OVER_LIMIT.md"
    f50001.write_bytes(b"A" * 50_001)
    res = load_instruction_file(f50001)
    assert res is not None
    assert res.truncated is True
    assert res.original_bytes == 50_001


def test_truncated_content_keeps_the_head(tmp_path: Path) -> None:
    file_path = tmp_path / "BIG.md"
    file_path.write_text("HEAD" + "A" * 2000, encoding="utf-8")
    res = load_instruction_file(file_path, max_bytes=500)
    assert res is not None
    assert res.content.startswith("HEAD")
