"""Phase 8 MCP: output limits (`utils/mcpValidation.ts`) and output storage
(`utils/mcpOutputStorage.ts`)."""

from __future__ import annotations

from typing import Any

import pytest

from app.loop.mcp.output import (
    DEFAULT_MAX_MCP_OUTPUT_TOKENS,
    IMAGE_TOKEN_ESTIMATE,
    extension_for_mime_type,
    get_binary_blob_saved_message,
    get_content_size_estimate,
    get_format_description,
    get_large_output_instructions,
    get_max_mcp_output_tokens,
    mcp_content_needs_truncation,
    persist_binary_content,
    truncate_mcp_content,
)


def test_the_limit_and_its_env(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", raising=False)
    assert get_max_mcp_output_tokens() == DEFAULT_MAX_MCP_OUTPUT_TOKENS == 25000
    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "50000")
    assert get_max_mcp_output_tokens() == 50000
    for bad in ("0", "-3", "abc"):
        monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", bad)
        assert get_max_mcp_output_tokens() == 25000


def test_size_estimate():
    assert get_content_size_estimate(None) == 0
    assert get_content_size_estimate("x" * 40) == 10
    blocks: list[dict[str, Any]] = [{"type": "text", "text": "x" * 8}, {"type": "image", "source": {}},
                                    {"type": "other"}]
    assert get_content_size_estimate(blocks) == 2 + IMAGE_TOKEN_ESTIMATE


def test_needs_truncation_only_over_the_limit(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "10")
    assert mcp_content_needs_truncation("x" * 40) is False
    assert mcp_content_needs_truncation("x" * 44) is True
    assert mcp_content_needs_truncation("") is False


def test_truncation_keeps_the_head_and_says_so(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "2")
    out = truncate_mcp_content("abcdefghijkl")
    assert isinstance(out, str)
    assert out.startswith("abcdefgh\n\n[OUTPUT TRUNCATED - exceeded 2 token limit]\n")
    assert "use them to retrieve specific portions of the data" in out
    blocks = truncate_mcp_content([{"type": "text", "text": "abcde"}, {"type": "text", "text": "fghij"},
                                   {"type": "text", "text": "zz"}])
    assert isinstance(blocks, list)
    assert blocks[:2] == [{"type": "text", "text": "abcde"}, {"type": "text", "text": "fgh"}]
    assert blocks[-1]["text"].startswith("\n\n[OUTPUT TRUNCATED")
    assert truncate_mcp_content(None) is None


def test_format_descriptions():
    assert get_format_description("toolResult") == "Plain text"
    assert get_format_description("structuredContent", "{a: number}") == "JSON with schema: {a: number}"
    assert get_format_description("structuredContent") == "JSON"
    assert get_format_description("contentArray", "[{type: string}]") == "JSON array with schema: [{type: string}]"
    assert get_format_description("contentArray") == "JSON array"


def test_large_output_instructions_name_the_file():
    text = get_large_output_instructions("/r/out.txt", 123456, "Plain text")
    assert text.startswith("Error: result (123,456 characters) exceeds maximum allowed tokens. "
                           "Output has been saved to /r/out.txt.\nFormat: Plain text\n")
    assert "You MUST read the content from the file at /r/out.txt in sequential chunks" in text
    assert "reduce the chunk size until you have read 100% of the content without truncation.\n" in text


@pytest.mark.parametrize(("mime", "ext"), [
    (None, "bin"), ("application/pdf", "pdf"), ("image/png; charset=x", "png"), ("IMAGE/JPEG", "jpg"),
    ("audio/mpeg", "mp3"), ("weird/type", "bin"),
])
def test_extensions(mime, ext):
    assert extension_for_mime_type(mime) == ext


def test_binary_content_is_saved_under_the_runs_tool_results(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    path, size = persist_binary_content(b"\x89PNG..", "image/png", "mcp-s-blob-1", run_id="r1", cwd="/w")
    assert path.read_bytes() == b"\x89PNG.." and size == 6 and path.suffix == ".png"
    assert "tool-results" in str(path) and "r1" in str(path)
    assert get_binary_blob_saved_message(str(path), "image/png", 6, "[Image from s] ") == (
        f"[Image from s] Binary content (image/png, 6 bytes) saved to {path}")
    assert get_binary_blob_saved_message("/p", None, 2048, "") == "Binary content (unknown type, 2KB) saved to /p"


# ── mutation-gate pins (Phase 8) ────────────────────────────────────────────

def test_the_large_output_instructions_are_upstreams_verbatim():
    from app.loop.mcp.output import get_large_output_instructions

    assert get_large_output_instructions("/p", 1234, "Plain text") == (
        "Error: result (1,234 characters) exceeds maximum allowed tokens. Output has been saved to /p.\n"
        "Format: Plain text\n"
        "Use offset and limit parameters to read specific portions of the file, search within it for specific "
        "content, and jq to make structured queries.\n"
        "REQUIREMENTS FOR SUMMARIZATION/ANALYSIS/REVIEW:\n"
        "- You MUST read the content from the file at /p in sequential chunks until 100% of the content has been "
        "read.\n"
        "- If you receive truncation warnings when reading the file, reduce the chunk size until you have read 100% "
        "of the content without truncation.\n"
        "- Before producing ANY summary or analysis, you MUST explicitly describe what portion of the content you "
        "have read. ***If you did not read the entire content, you MUST explicitly state this.***\n")


def test_the_truncation_notice_is_upstreams_verbatim(monkeypatch):
    from app.loop.mcp.output import truncate_mcp_content

    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "1")
    out = truncate_mcp_content([{"type": "text", "text": "abcdef"}])
    assert out == [{"type": "text", "text": "abcd"}, {"type": "text", "text": (
        "\n\n[OUTPUT TRUNCATED - exceeded 1 token limit]\n"
        "The tool output was truncated. If this MCP server provides pagination or filtering tools, use them to "
        "retrieve specific portions of the data. If pagination is not available, inform the user that you are "
        "working with truncated output and results may be incomplete.")}]


def test_block_truncation_boundaries():
    from app.loop.mcp.output import IMAGE_TOKEN_ESTIMATE, _truncate_blocks

    image = {"type": "image", "source": {}}
    other = {"type": "other"}
    # Text that exactly fills the budget is kept whole; the next text block is not started.
    assert _truncate_blocks([{"type": "text", "text": "ab"}, {"type": "text", "text": "c"}, other], 2) == [
        {"type": "text", "text": "ab"}]
    # One character left still admits one character.
    assert _truncate_blocks([{"type": "text", "text": "a"}, {"type": "text", "text": "bc"}], 2) == [
        {"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    # The budget is cumulative across blocks.
    assert _truncate_blocks([{"type": "text", "text": "ab"}, {"type": "text", "text": "cd"},
                             {"type": "text", "text": "ef"}], 5) == [
        {"type": "text", "text": "ab"}, {"type": "text", "text": "cd"}, {"type": "text", "text": "e"}]
    # A missing text is empty.
    assert _truncate_blocks([{"type": "text"}], 2) == [{"type": "text"}]
    # Images cost IMAGE_TOKEN_ESTIMATE * 4 characters, an exact fit included, and accumulate.
    size = IMAGE_TOKEN_ESTIMATE * 4
    assert _truncate_blocks([image], size) == [image]
    assert _truncate_blocks([image], size - 1) == []
    assert _truncate_blocks([image, image], size * 2 - 1) == [image]
    assert _truncate_blocks([image, {"type": "text", "text": "x"}], size) == [image]
    # Other blocks pass through.
    assert _truncate_blocks([other], 0) == [other]


def test_the_size_estimate_sums_text_and_images():
    from app.loop.mcp.output import IMAGE_TOKEN_ESTIMATE, get_content_size_estimate

    assert get_content_size_estimate([{"type": "text", "text": "a" * 8}, {"type": "text", "text": "b" * 8},
                                      {"type": "other", "text": "c" * 400}]) == 4
    assert get_content_size_estimate([{"type": "text"}, {"type": "image"}]) == IMAGE_TOKEN_ESTIMATE


def test_the_output_limit_and_its_threshold(monkeypatch):
    from app.loop.mcp.output import (
        get_max_mcp_output_tokens,
        mcp_content_needs_truncation,
    )

    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "1")
    assert get_max_mcp_output_tokens() == 1
    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "4")
    # An estimate at exactly half the limit needs nothing (8 chars ≈ 2 tokens).
    assert mcp_content_needs_truncation("a" * 8) is False


def test_an_exact_text_fit_keeps_going_and_images_accumulate():
    from app.loop.mcp.output import IMAGE_TOKEN_ESTIMATE, _truncate_blocks

    other = {"type": "other"}
    image = {"type": "image", "source": {}}
    assert _truncate_blocks([{"type": "text", "text": "ab"}, other], 2) == [{"type": "text", "text": "ab"}, other]
    size = IMAGE_TOKEN_ESTIMATE * 4
    assert _truncate_blocks([{"type": "text", "text": "a"}, image, image], 2 * size) == [
        {"type": "text", "text": "a"}, image]
