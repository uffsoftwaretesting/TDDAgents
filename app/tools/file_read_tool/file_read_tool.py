"""
ReadFile: a file's lines, numbered, from a 1-based offset; images, PDFs and notebooks natively.

Ported from `reference/claude-code/src/tools/FileReadTool/FileReadTool.ts` -> `FileReadTool`
(`validateInput`, `callInner`, `readImageWithTokenBudget`, `createImageResponse`,
`mapToolResultToToolResultBlockParam`); input contract `FileReadInput` (`sdk-tools.d.ts`).

- **Images** are the tool result's content: an image block (`ToolResult.content_blocks`), plus a
  meta message with the dimensions when the image was resized. A provider whose tool messages
  cannot carry images gets the block moved to a meta user message at the request
  (`move_tool_result_media_to_user`).
- **PDFs** are a short text result plus a meta user message: the whole document as a `file`
  block, or with `pages` the range rendered to images by `pdftoppm`.
- **Notebooks** are their cells as text and image blocks (`utils/notebook.py`).

Binary formats are read from the project's host path (`LocalWorkspace.resolve`), as upstream
reads its filesystem; a workspace without one (E2B) reads text only.
"""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.constants.api_limits import (
    PDF_AT_MENTION_INLINE_THRESHOLD,
    PDF_MAX_PAGES_PER_READ,
)
from app.constants.files import has_binary_extension
from app.tool import BuiltTool, ToolContext, ToolResult, ValidationResult, build_tool
from app.tools.utils import (
    NO_WORKSPACE,
    VALID,
    activate_conditional_skills,
    file_path_permission_matcher,
    get_prompt,
    host_path,
    invalid,
    is_non_negative_int,
    read_host_path,
    read_state_key,
    read_text,
)
from app.utils.file import FILE_NOT_FOUND_CWD_NOTE, add_line_numbers
from app.utils.file_state_cache import FileState
from app.utils.frontmatter_parser import render_prompt
from app.utils.image_resizer import (
    ImageDimensions,
    ImageResizeError,
    compress_image_buffer_with_token_limit,
    create_image_metadata_text,
    detect_image_format_from_buffer,
    maybe_resize_and_downsample_image_buffer,
)
from app.utils.notebook import map_notebook_cells_to_blocks, read_notebook
from app.utils.pdf import PDFError, extract_pdf_pages, get_pdf_page_count, read_pdf
from app.utils.pdf_utils import is_pdf_extension, is_pdf_supported, parse_pdf_page_range
from app.utils.permissions.filesystem import (
    check_file_read_permissions,
    matching_rule_for_input,
    permission_cwd,
    workspace_absolute_path,
)
from app.utils.permissions.permissions import extract_permission_context
from app.utils.tool_result_storage import format_file_size, tool_results_dir
from app.workspace.base import workspace_path

PROMPT_PATH = Path(__file__).with_name("prompt.md")

#: `FileReadTool.validateInput`'s refusal of a Read-denied path (errorCode 1).
READ_DENIED_MESSAGE = "File is in a directory that is denied by your permission settings."

#: `FileReadTool/prompt.ts` -> `MAX_LINES_TO_READ`.
MAX_LINES_TO_READ = 2000

#: `FileReadTool.ts` -> `IMAGE_EXTENSIONS`.
IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})
#: `limits.ts` -> `DEFAULT_MAX_OUTPUT_TOKENS`: the token budget an image or notebook must fit.
DEFAULT_MAX_OUTPUT_TOKENS = 25000
#: `utils/file.ts` -> `MAX_OUTPUT_SIZE`: the largest notebook content read.
MAX_OUTPUT_SIZE = 256 * 1024
#: Upstream's `pages` description (`FileReadTool.ts` input schema).
PAGES_DESCRIPTION = (f'Page range for PDF files (e.g., "1-5", "3", "10-20"). Only applicable to PDF files. '
                     f"Maximum {PDF_MAX_PAGES_PER_READ} pages per request.")


class ReadFileError(Exception):
    """An error `callInner` throws: reported as the call's error result."""


def _extension(file_path: str) -> str:
    return Path(file_path).suffix.lower().removeprefix(".")


def _host_file(context: ToolContext, file_path: str) -> Path:
    """The file on the host, for the binary formats (`read_host_path`, else `LocalWorkspace.resolve`)."""
    outside = read_host_path(context, file_path)
    if outside is not None:
        if not outside.is_file():
            raise FileNotFoundError(file_path)
        return outside
    resolve = getattr(context.workspace, "resolve", None)
    if not callable(resolve):
        raise ReadFileError("Images, PDFs and notebooks can only be read from a local workspace.")
    path = Path(resolve(workspace_path(context.workspace, file_path)))
    if not path.is_file():
        raise FileNotFoundError(file_path)
    return path


def _trigger_nested_memory(context: ToolContext, file_path: str) -> None:
    """`nestedMemoryAttachmentTriggers`: a project file's directories may hold nested memory."""
    if read_host_path(context, file_path) is None:
        context.nested_memory_attachment_triggers.add(workspace_path(context.workspace, file_path))


def _meta(content: Any) -> HumanMessage:
    return HumanMessage(content=content, additional_kwargs={"is_meta": True})


def _image_block(data: str, media_type: str) -> dict[str, Any]:
    return {"type": "image", "base64": data, "mime_type": media_type}


def read_image_with_token_budget(path: Path, max_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
                                 ) -> tuple[str, str, int, ImageDimensions | None]:
    """`readImageWithTokenBudget`: (base64, media type, original size, dimensions)."""
    buffer = path.read_bytes()
    original_size = len(buffer)
    if original_size == 0:
        raise ReadFileError(f"Image file is empty: {path}")
    detected = detect_image_format_from_buffer(buffer)
    detected_format = detected.split("/")[1] or "png"
    try:
        resized = maybe_resize_and_downsample_image_buffer(buffer, original_size, detected_format)
        data, media, dims = base64.b64encode(resized.buffer).decode("ascii"), resized.media_type, resized.dimensions
    except ImageResizeError:
        raise
    except Exception:  # noqa: BLE001 - upstream logs and sends the original
        data, media, dims = base64.b64encode(buffer).decode("ascii"), detected_format, None
    if math.ceil(len(data) * 0.125) > max_tokens:
        compressed = compress_image_buffer_with_token_limit(buffer, max_tokens, detected)
        return compressed.base64, compressed.media_type, original_size, None
    return data, f"image/{media}", original_size, dims


def _read_notebook(context: ToolContext, file_path: str, content: str) -> ToolResult:
    cells = read_notebook(content)
    cells_json = json.dumps(cells, ensure_ascii=False)
    size = len(cells_json.encode("utf-8"))
    if size > MAX_OUTPUT_SIZE:
        raise ReadFileError(
            f"Notebook content ({format_file_size(size)}) exceeds maximum allowed size "
            f"({format_file_size(MAX_OUTPUT_SIZE)}). Use Bash with jq to read specific portions:\n"
            f'  cat "{file_path}" | jq \'.cells[:20]\' # First 20 cells\n'
            f'  cat "{file_path}" | jq \'.cells[100:120]\' # Cells 100-120\n'
            f'  cat "{file_path}" | jq \'.cells | length\' # Count total cells\n'
            f'  cat "{file_path}" | jq \'.cells[] | select(.cell_type=="code") | .source\' # All code sources')
    context.read_file_state[read_state_key(context, file_path)] = FileState(content=cells_json)
    _trigger_nested_memory(context, file_path)
    blocks = map_notebook_cells_to_blocks(cells)
    text = "\n".join(b["text"] for b in blocks if b["type"] == "text")
    return ToolResult(content=text, content_blocks=tuple(blocks))


def _read_image(context: ToolContext, file_path: str) -> ToolResult:
    data, media_type, original_size, dims = read_image_with_token_budget(_host_file(context, file_path))
    _trigger_nested_memory(context, file_path)
    metadata = create_image_metadata_text(dims) if dims else None
    return ToolResult(
        content=f"Image file read: {file_path} ({media_type}, {format_file_size(original_size)})",
        content_blocks=(_image_block(data, media_type),),
        new_messages=(_meta(metadata),) if metadata else (),
    )


def _pdf_error(error: PDFError) -> ReadFileError:
    return ReadFileError(error.message)


def _read_pdf(context: ToolContext, file_path: str, pages: str | None) -> ToolResult:
    host = _host_file(context, file_path)
    if pages:
        output_root = tool_results_dir(context.run_id or "session", permission_cwd(context))
        extracted = extract_pdf_pages(host, output_root, parse_pdf_page_range(pages))
        if isinstance(extracted, PDFError):
            raise _pdf_error(extracted)
        blocks = []
        for image in sorted(f for f in extracted.output_dir.iterdir() if f.name.endswith(".jpg")):
            buffer = image.read_bytes()
            resized = maybe_resize_and_downsample_image_buffer(buffer, len(buffer), "jpeg")
            blocks.append(_image_block(base64.b64encode(resized.buffer).decode("ascii"),
                                       f"image/{resized.media_type}"))
        return ToolResult(
            content=(f"PDF pages extracted: {extracted.count} page(s) from {file_path} "
                     f"({format_file_size(extracted.original_size)})"),
            new_messages=(_meta(blocks),) if blocks else (),
        )
    page_count = get_pdf_page_count(host)
    if page_count is not None and page_count > PDF_AT_MENTION_INLINE_THRESHOLD:
        raise ReadFileError(
            f"This PDF has {page_count} pages, which is too many to read at once. "
            'Use the pages parameter to read specific page ranges (e.g., pages: "1-5"). '
            f"Maximum {PDF_MAX_PAGES_PER_READ} pages per request.")
    if not is_pdf_supported(str(getattr(context, "main_loop_model", "") or "")):
        raise ReadFileError(
            "Reading full PDFs is not supported with this model. Use a newer model (Sonnet 3.5 v2 or later), "
            'or use the pages parameter to read specific page ranges (e.g., pages: "1-5", maximum '
            f"{PDF_MAX_PAGES_PER_READ} pages per request). Page extraction requires poppler-utils: install with "
            "`brew install poppler` on macOS or `apt-get install poppler-utils` on Debian/Ubuntu.")
    read = read_pdf(host)
    if isinstance(read, PDFError):
        raise _pdf_error(read)
    document = {"type": "file", "base64": read.base64, "mime_type": "application/pdf", "filename": host.name}
    return ToolResult(
        content=f"PDF file read: {file_path} ({format_file_size(read.original_size)})",
        new_messages=(_meta([document]),),
    )


def build_read_file_tool() -> BuiltTool:
    def validate_read(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
        # `FileReadTool.validateInput`: a Read-deny match is refused before any I/O.
        cwd = permission_cwd(context)
        target = workspace_absolute_path(str(input_args.get("file_path") or ""), cwd)
        if matching_rule_for_input(target, extract_permission_context(context), "read", "deny", cwd) is not None:
            return invalid(READ_DENIED_MESSAGE, 1)
        pages = input_args.get("pages")
        if pages is not None:
            parsed = parse_pdf_page_range(str(pages))
            if parsed is None:
                return invalid(f'Invalid pages parameter: "{pages}". Use formats like "1-5", "3", or "10-20". '
                               "Pages are 1-indexed.", 7)
            first, last = parsed
            range_size = PDF_MAX_PAGES_PER_READ + 1 if last == math.inf else int(last) - first + 1
            if range_size > PDF_MAX_PAGES_PER_READ:
                return invalid(f'Page range "{pages}" exceeds maximum of {PDF_MAX_PAGES_PER_READ} pages per request. '
                               "Please use a smaller range.", 8)
        ext = Path(target).suffix.lower()
        if has_binary_extension(target) and not is_pdf_extension(ext) and ext.removeprefix(".") not in IMAGE_EXTENSIONS:
            return invalid(f"This tool cannot read binary files. The file appears to be a binary {ext} file. "
                           "Please use appropriate tools for binary file analysis.", 4)
        if "offset" in input_args and not is_non_negative_int(input_args["offset"]):
            return invalid("offset must be a non-negative integer.", 0)
        limit = input_args.get("limit")
        if "limit" in input_args and not (is_non_negative_int(limit) and limit != 0):
            return invalid("limit must be a positive integer.", 0)
        return VALID

    async def call_read(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = str(input_args.get("file_path") or "")
        offset = int(input_args.get("offset", 1))
        limit = input_args.get("limit")
        ws = context.workspace
        if ws is None:
            return NO_WORKSPACE

        ext = _extension(file_path)
        if host_path(context, file_path) is None and (ext in IMAGE_EXTENSIONS or is_pdf_extension(ext)):
            try:
                if ext in IMAGE_EXTENSIONS:
                    return _read_image(context, file_path)
                return _read_pdf(context, file_path, input_args.get("pages"))
            except FileNotFoundError:
                return ToolResult(content=f"File does not exist. {FILE_NOT_FOUND_CWD_NOTE}", is_error=True)
            except Exception as e:  # noqa: BLE001 - deliberate catch-all (upstream's call throws)
                return ToolResult(content=f"Error reading file: {e}", is_error=True)

        try:
            content = read_text(ws, file_path, context)
        except Exception as e:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return ToolResult(content=f"Error reading file: {e}", is_error=True)
        if content is None:
            return ToolResult(content=f"File does not exist. {FILE_NOT_FOUND_CWD_NOTE}", is_error=True)
        if ext == "ipynb":
            try:
                return _read_notebook(context, file_path, content)
            except Exception as e:  # noqa: BLE001 - deliberate catch-all (upstream's call throws)
                return ToolResult(content=f"Error reading file: {e}", is_error=True)

        all_lines = content.splitlines()
        line_offset = 0 if offset == 0 else offset - 1
        window = all_lines[line_offset:line_offset + (limit if limit is not None else MAX_LINES_TO_READ)]
        partial = "offset" in input_args or "limit" in input_args
        context.read_file_state[read_state_key(context, file_path)] = FileState(
            content=content,
            offset=offset if partial else None,
            limit=limit if partial else None,
        )
        # `FileReadTool`: the file's directories may hold nested memory to attach next turn.
        _trigger_nested_memory(context, file_path)
        activate_conditional_skills(context, file_path)

        if window:
            return ToolResult(content=add_line_numbers(window, max(offset, 1)))
        if not all_lines:
            return ToolResult(
                content="<system-reminder>Warning: the file exists but the contents are empty.</system-reminder>"
            )
        return ToolResult(
            content=(
                "<system-reminder>Warning: the file exists but is shorter than the provided offset "
                f"({offset}). The file has {len(all_lines)} lines.</system-reminder>"
            )
        )

    return build_tool(
        name="ReadFile",
        # `renderPromptTemplate` with the default limits: no max-size line, the default offset line.
        prompt=render_prompt(get_prompt(PROMPT_PATH, "Reads a file."), {"MAX_LINES_TO_READ": MAX_LINES_TO_READ},
                             strip_comments=False),
        # `FileReadTool.ts`: Infinity, because persisting a read would create a
        # ReadFile -> file -> ReadFile loop and the tool already bounds its own output.
        max_result_size_chars=math.inf,
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to read"},
                "offset": {
                    "type": "integer",
                    "description": "The line number to start reading from. "
                    "Only provide if the file is too large to read at once",
                },
                "limit": {
                    "type": "integer",
                    "description": "The number of lines to read. "
                    "Only provide if the file is too large to read at once.",
                },
                "pages": {"type": "string", "description": PAGES_DESCRIPTION},
            },
            "required": ["file_path"],
        },
        description=lambda args: f"Read file: {args.get('file_path')}",
        is_read_only=lambda args: True,
        is_concurrency_safe=lambda args: True,
        validate_input=validate_read,
        check_permissions=check_file_read_permissions("file_path"),
        call=call_read,
        prepare_permission_matcher=file_path_permission_matcher,
    )
