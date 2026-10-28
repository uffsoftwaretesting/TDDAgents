"""
ReadFile's images, PDFs and notebooks (2026-10-28, ports of `FileReadTool.callInner`,
`utils/imageResizer.ts`, `utils/pdf.ts`, `utils/pdfUtils.ts`, `utils/notebook.ts`), and the
request seam that moves a tool result's images to a user message for OpenAI.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import shutil
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolCall, ToolMessage
from PIL import Image

from app.constants.files import has_binary_extension
from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppState, AppStateStore
from app.tool import ToolResult, default_map_result, tool_context_for
from app.tools.file_read_tool.file_read_tool import build_read_file_tool
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils import pdf as pdf_module
from app.utils.image_resizer import (
    ImageDimensions,
    compress_image_buffer,
    create_image_metadata_text,
    detect_image_format_from_buffer,
    maybe_resize_and_downsample_image_buffer,
)
from app.utils.messages import TOOL_RESULT_MEDIA_MOVED, move_tool_result_media_to_user
from app.utils.notebook import (
    format_output,
    map_notebook_cells_to_blocks,
    read_notebook,
)
from app.utils.pdf_utils import is_pdf_extension, is_pdf_supported, parse_pdf_page_range
from app.utils.tokens import MEDIA_BLOCK_TOKENS, estimate_block_tokens
from app.workspace.local import LocalWorkspace

needs_poppler = pytest.mark.skipif(shutil.which("pdftoppm") is None or shutil.which("pdfinfo") is None,
                                   reason="poppler-utils is not installed")


def png(width: int = 10, height: int = 10, color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


def pdf(pages: int) -> bytes:
    images = [Image.new("RGB", (200, 100), (i * 20 % 255, 50, 90)) for i in range(pages)]
    out = io.BytesIO()
    images[0].save(out, format="PDF", save_all=True, append_images=images[1:])
    return out.getvalue()


# ── pure helpers ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pages, expected", [
    ("5", (5, 5)), ("1-10", (1, 10)), ("3-", (3, math.inf)), (" 2 - 4 ", (2, 4)),
    ("", None), ("0", None), ("x", None), ("5-3", None), ("0-", None), ("2-x", None),
])
def test_parse_pdf_page_range(pages: str, expected: Any):
    assert parse_pdf_page_range(pages) == expected


def test_pdf_utils():
    assert is_pdf_extension(".PDF") and is_pdf_extension("pdf") and not is_pdf_extension("txt")
    assert is_pdf_supported("gpt-5-mini") and not is_pdf_supported("claude-3-haiku-20240307")


@pytest.mark.parametrize("path, binary", [("a.zip", True), ("a.PNG", True), ("a.pdf", True), ("a.py", False),
                                          ("Makefile", False)])
def test_has_binary_extension(path: str, binary: bool):
    assert has_binary_extension(path) is binary


@pytest.mark.parametrize("data, media", [
    (b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff\xe0", "image/jpeg"), (b"GIF89a", "image/gif"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"), (b"xyz", "image/png"), (b"????", "image/png"),
])
def test_detect_image_format(data: bytes, media: str):
    assert detect_image_format_from_buffer(data) == media


def test_a_small_image_passes_through_with_its_dimensions():
    data = png(40, 20)
    result = maybe_resize_and_downsample_image_buffer(data, len(data), "png")
    assert result.buffer == data and result.media_type == "png"
    assert result.dimensions == ImageDimensions(40, 20, 40, 20)


def test_an_oversized_image_is_scaled_into_the_limits():
    data = png(3000, 1000)
    result = maybe_resize_and_downsample_image_buffer(data, len(data), "png")
    assert result.dimensions == ImageDimensions(3000, 1000, 2000, 667)
    assert Image.open(io.BytesIO(result.buffer)).size == (2000, 667)


def test_compression_fits_a_byte_budget():
    noisy = Image.effect_noise((600, 600), 100).convert("RGB")
    out = io.BytesIO()
    noisy.save(out, format="PNG")
    data = out.getvalue()
    result = compress_image_buffer(data, max_bytes=len(data) // 4, original_media_type="image/png")
    assert len(base64.b64decode(result.base64)) <= len(data) // 4 and result.original_size == len(data)


def test_image_metadata_text():
    assert create_image_metadata_text(ImageDimensions(40, 20, 40, 20)) is None
    assert create_image_metadata_text(ImageDimensions(3000, 1000, 2000, 667)) == (
        "[Image: original 3000x1000, displayed at 2000x667. Multiply coordinates by 1.50 to map to original image.]")
    assert create_image_metadata_text(ImageDimensions(), "/x.png") == "[Image source: /x.png]"


NOTEBOOK = {
    "metadata": {"language_info": {"name": "python"}},
    "cells": [
        {"cell_type": "markdown", "id": "m1", "source": ["# Title"]},
        {"cell_type": "code", "execution_count": 1, "source": "print(1)", "outputs": [
            {"output_type": "stream", "text": ["1\n"]},
            {"output_type": "display_data", "data": {"text/plain": "<fig>", "image/png": "iVBO RW0K"}},
            {"output_type": "error", "ename": "ValueError", "evalue": "bad", "traceback": ["t1", "t2"]},
        ]},
    ],
}


def test_a_notebook_maps_to_cells_and_images():
    cells = read_notebook(json.dumps(NOTEBOOK))
    assert cells[0]["cell_id"] == "m1" and cells[1]["cell_id"] == "cell-1"
    assert cells[1]["outputs"][1]["image"] == {"image_data": "iVBORW0K", "media_type": "image/png"}
    blocks = map_notebook_cells_to_blocks(cells)
    assert blocks == [
        {"type": "text", "text": '<cell id="m1"><cell_type>markdown</cell_type># Title</cell id="m1">\n'
                                 '<cell id="cell-1">print(1)</cell id="cell-1">\n\n1\n\n\n<fig>'},
        {"type": "image", "base64": "iVBORW0K", "mime_type": "image/png"},
        {"type": "text", "text": "\nValueError: bad\nt1\nt2"},
    ]


def test_large_outputs_are_replaced_by_a_jq_hint():
    notebook = {"cells": [{"cell_type": "code", "source": "x", "outputs": [
        {"output_type": "stream", "text": "y" * 20000}]}], "metadata": {}}
    (cell,) = read_notebook(json.dumps(notebook))
    assert cell["outputs"] == [{"output_type": "stream", "text": (
        "Outputs are too large to include. Use Bash with: cat <notebook_path> | jq '.cells[0].outputs'")}]
    with pytest.raises(ValueError, match='Cell with ID "nope" not found'):
        read_notebook(json.dumps(notebook), cell_id="nope")


def test_format_output_truncates_past_the_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "10")
    assert format_output("short") == "short"
    assert format_output("0123456789abc\nd\ne") == "0123456789\n\n... [3 lines truncated] ..."


def test_media_blocks_cost_a_fixed_estimate():
    assert estimate_block_tokens({"type": "image", "base64": "x" * 100_000}) == MEDIA_BLOCK_TOKENS
    assert estimate_block_tokens({"type": "file", "base64": "x" * 100_000}) == MEDIA_BLOCK_TOKENS


# ── the request seam ─────────────────────────────────────────────────────────

IMAGE = {"type": "image", "base64": "AAAA", "mime_type": "image/png"}


def test_content_blocks_become_the_tool_messages_content():
    message = default_map_result(ToolResult(content="text form", content_blocks=(IMAGE,)), "t1")
    assert message.content == [IMAGE]
    assert default_map_result(ToolResult(content="plain"), "t1").content == "plain"


def test_images_move_after_the_batch_for_providers_without_tool_images():
    call = AIMessage(content="", tool_calls=[ToolCall(name="ReadFile", args={}, id="a"),
                                             ToolCall(name="ReadFile", args={}, id="b")])
    messages = [call,
                ToolMessage(content=[IMAGE], tool_call_id="a"),
                ToolMessage(content=[{"type": "text", "text": "cells"}, IMAGE], tool_call_id="b"),
                AIMessage(content="seen")]
    moved = move_tool_result_media_to_user(messages)
    assert [type(m).__name__ for m in moved] == ["AIMessage", "ToolMessage", "ToolMessage", "HumanMessage",
                                                 "AIMessage"]
    assert moved[1].content == TOOL_RESULT_MEDIA_MOVED
    assert moved[2].content == [{"type": "text", "text": "cells"}]
    assert moved[3].content == [IMAGE, IMAGE] and moved[3].additional_kwargs == {"is_meta": True}
    assert messages[1].content == [IMAGE]  # the stored history is untouched
    tail = move_tool_result_media_to_user([ToolMessage(content=[IMAGE], tool_call_id="a")])
    assert isinstance(tail[-1], HumanMessage) and tail[-1].content == [IMAGE]


# ── through the tool pipeline ────────────────────────────────────────────────

def read(tmp_path: Path, args: dict[str, Any]) -> Any:
    ws = LocalWorkspace(str(tmp_path / "project"))
    perm = ToolPermissionContext(mode=PermissionMode.DEFAULT, should_avoid_permission_prompts=True)
    ctx = tool_context_for(AppStateStore(AppState(tool_permission_context=perm)), workspace=ws, run_id="r1")
    tool = build_read_file_tool()
    ctx.tools = (tool,)
    call = ToolCall(id="c1", name=tool.name, args=args)
    return asyncio.run(run_tool_use(call, AIMessage(content="", tool_calls=[call]), ctx, (tool,))), ws


def write(tmp_path: Path, name: str, data: bytes) -> str:
    path = tmp_path / "project" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def test_reading_an_image(tmp_path: Path):
    path = write(tmp_path, "shot.png", png(3000, 1000))
    outcome, _ = read(tmp_path, {"file_path": path})
    message = outcome.message
    assert message.status == "success"
    (block,) = message.content
    assert block["type"] == "image" and block["mime_type"] == "image/png"
    assert Image.open(io.BytesIO(base64.b64decode(block["base64"]))).size == (2000, 667)
    (meta,) = [m for m in outcome.extra_messages if isinstance(m, HumanMessage)]
    assert str(meta.content).startswith("[Image: original 3000x1000, displayed at 2000x667.")


def test_reading_a_missing_image(tmp_path: Path):
    outcome, _ = read(tmp_path, {"file_path": str(tmp_path / "project" / "nope.png")})
    assert outcome.message.status == "error" and "File does not exist." in outcome.message.content


@needs_poppler
def test_reading_a_whole_pdf(tmp_path: Path):
    path = write(tmp_path, "doc.pdf", pdf(2))
    outcome, _ = read(tmp_path, {"file_path": path})
    assert outcome.message.content.startswith(f"PDF file read: {path} (")
    (meta,) = [m for m in outcome.extra_messages if isinstance(m, HumanMessage)]
    assert isinstance(meta.content, list)
    (document,) = meta.content
    assert isinstance(document, dict)
    assert document["type"] == "file" and document["mime_type"] == "application/pdf"
    assert base64.b64decode(document["base64"]).startswith(b"%PDF-")


@needs_poppler
def test_a_long_pdf_needs_pages_and_a_range_renders_images(tmp_path: Path):
    path = write(tmp_path, "long.pdf", pdf(12))
    refused, _ = read(tmp_path, {"file_path": path})
    assert refused.message.status == "error" and "This PDF has 12 pages, which is too many" in refused.message.content
    outcome, _ = read(tmp_path, {"file_path": path, "pages": "2-4"})
    assert outcome.message.content.startswith(f"PDF pages extracted: 3 page(s) from {path} (")
    (meta,) = [m for m in outcome.extra_messages if isinstance(m, HumanMessage)]
    assert isinstance(meta.content, list) and len(meta.content) == 3
    assert all(isinstance(b, dict) and b["type"] == "image" and b["mime_type"] == "image/jpeg"
               for b in meta.content)


def test_pages_without_pdftoppm_reports_upstreams_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pdf_module, "is_pdftoppm_available", lambda: False)
    path = write(tmp_path, "doc.pdf", pdf(1))
    outcome, _ = read(tmp_path, {"file_path": path, "pages": "1"})
    assert outcome.message.status == "error" and "pdftoppm is not installed." in outcome.message.content


def test_an_invalid_pdf_is_refused(tmp_path: Path):
    path = write(tmp_path, "fake.pdf", b"<html>not a pdf</html>")
    outcome, _ = read(tmp_path, {"file_path": path})
    assert outcome.message.status == "error" and "missing %PDF- header" in outcome.message.content


def test_reading_a_notebook(tmp_path: Path):
    path = write(tmp_path, "nb.ipynb", json.dumps(NOTEBOOK).encode())
    outcome, _ = read(tmp_path, {"file_path": path})
    assert outcome.message.content == map_notebook_cells_to_blocks(read_notebook(json.dumps(NOTEBOOK)))


@pytest.mark.parametrize("args, code, message", [
    ({"pages": "x"}, 7, 'Invalid pages parameter: "x". Use formats like "1-5", "3", or "10-20". Pages are 1-indexed.'),
    ({"pages": "1-21"}, 8, 'Page range "1-21" exceeds maximum of 20 pages per request. Please use a smaller range.'),
    ({"pages": "3-"}, 8, 'Page range "3-" exceeds maximum of 20 pages per request. Please use a smaller range.'),
])
def test_pages_are_validated(tmp_path: Path, args: dict[str, Any], code: int, message: str):
    outcome, _ = read(tmp_path, {"file_path": write(tmp_path, "doc.pdf", b"%PDF-1.4"), **args})
    assert outcome.message.status == "error" and message in outcome.message.content


def test_a_binary_file_is_refused(tmp_path: Path):
    outcome, _ = read(tmp_path, {"file_path": write(tmp_path, "a.zip", b"PK\x03\x04")})
    assert outcome.message.content.endswith(
        "This tool cannot read binary files. The file appears to be a binary .zip file. "
        "Please use appropriate tools for binary file analysis.</tool_use_error>")
