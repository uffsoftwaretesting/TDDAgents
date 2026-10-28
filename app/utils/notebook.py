"""
Reading Jupyter notebooks for ReadFile.

Ported from `reference/claude-code/src/utils/notebook.ts`: `readNotebook` (with `processCell`,
`processOutput`, `extractImage`, `isLargeOutputs`) and `mapNotebookCellsToToolResult`. Output text
is cut as the Bash tool's `formatOutput` cuts it (`tools/BashTool/utils.ts`, the
`BASH_MAX_OUTPUT_LENGTH` limit of `utils/shell/outputLimits.ts`).

Blocks are LangChain's standard content blocks (`{"type": "text"}`, `{"type": "image",
"base64", "mime_type"}`), which both providers translate; upstream's are Anthropic's.
"""

from __future__ import annotations

import json
import os
from typing import Any

#: `notebook.ts` -> `LARGE_OUTPUT_THRESHOLD`.
LARGE_OUTPUT_THRESHOLD = 10000
#: `outputLimits.ts` -> `BASH_MAX_OUTPUT_DEFAULT` / `BASH_MAX_OUTPUT_UPPER_LIMIT`.
BASH_MAX_OUTPUT_DEFAULT = 30_000
BASH_MAX_OUTPUT_UPPER_LIMIT = 150_000


def get_max_output_length() -> int:
    """`getMaxOutputLength`: `BASH_MAX_OUTPUT_LENGTH`, bounded, else the default."""
    raw = os.environ.get("BASH_MAX_OUTPUT_LENGTH")
    try:
        value = int(raw) if raw is not None else BASH_MAX_OUTPUT_DEFAULT
    except ValueError:
        return BASH_MAX_OUTPUT_DEFAULT
    if value <= 0:
        return BASH_MAX_OUTPUT_DEFAULT
    return min(value, BASH_MAX_OUTPUT_UPPER_LIMIT)


def format_output(content: str) -> str:
    """`formatOutput`'s `truncatedContent`: past the limit, the rest is replaced by a line count."""
    if content.lower().startswith("data:image/"):
        return content
    limit = get_max_output_length()
    if len(content) <= limit:
        return content
    remaining_lines = content.count("\n", limit) + 1
    return f"{content[:limit]}\n\n... [{remaining_lines} lines truncated] ..."


def _output_text(text: Any) -> str:
    if not text:
        return ""
    return format_output("".join(text) if isinstance(text, list) else str(text))


def _extract_image(data: dict[str, Any]) -> dict[str, str] | None:
    for media_type in ("image/png", "image/jpeg"):
        value = data.get(media_type)
        if isinstance(value, str):
            return {"image_data": "".join(value.split()), "media_type": media_type}
    return None


def _process_output(output: dict[str, Any]) -> dict[str, Any] | None:
    kind = output.get("output_type")
    if kind == "stream":
        return {"output_type": kind, "text": _output_text(output.get("text"))}
    if kind in ("execute_result", "display_data"):
        data = output.get("data") or {}
        return {"output_type": kind, "text": _output_text(data.get("text/plain")),
                "image": _extract_image(data) if data else None}
    if kind == "error":
        traceback = "\n".join(output.get("traceback") or [])
        return {"output_type": kind,
                "text": _output_text(f"{output.get('ename')}: {output.get('evalue')}\n{traceback}")}
    return None


def _is_large(outputs: list[dict[str, Any] | None]) -> bool:
    size = 0
    for output in outputs:
        if not output:
            continue
        image = output.get("image")
        size += len(output.get("text") or "") + (len(image["image_data"]) if image else 0)
        if size > LARGE_OUTPUT_THRESHOLD:
            return True
    return False


def _process_cell(cell: dict[str, Any], index: int, language: str, include_large: bool) -> dict[str, Any]:
    source = cell.get("source", "")
    data: dict[str, Any] = {
        "cellType": cell.get("cell_type"),
        "source": "".join(source) if isinstance(source, list) else source,
        "execution_count": (cell.get("execution_count") or None) if cell.get("cell_type") == "code" else None,
        "cell_id": cell.get("id") or f"cell-{index}",
    }
    if cell.get("cell_type") == "code":
        data["language"] = language
        outputs = cell.get("outputs") or []
        if outputs:
            processed = [_process_output(o) for o in outputs]
            if not include_large and _is_large(processed):
                data["outputs"] = [{
                    "output_type": "stream",
                    "text": ("Outputs are too large to include. Use Bash with: "
                             f"cat <notebook_path> | jq '.cells[{index}].outputs'"),
                }]
            else:
                data["outputs"] = processed
    return data


def read_notebook(content: str, cell_id: str | None = None) -> list[dict[str, Any]]:
    """`readNotebook`: the notebook's cells, processed (one cell when `cell_id` is given)."""
    notebook = json.loads(content)
    language = ((notebook.get("metadata") or {}).get("language_info") or {}).get("name") or "python"
    cells = notebook.get("cells") or []
    if cell_id:
        for index, cell in enumerate(cells):
            if cell.get("id") == cell_id:
                return [_process_cell(cell, index, language, True)]
        raise ValueError(f'Cell with ID "{cell_id}" not found in notebook')
    return [_process_cell(cell, index, language, False) for index, cell in enumerate(cells)]


def _cell_blocks(cell: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = []
    if cell["cellType"] != "code":
        metadata.append(f"<cell_type>{cell['cellType']}</cell_type>")
    if cell["cellType"] == "code" and cell.get("language") != "python":
        metadata.append(f"<language>{cell.get('language')}</language>")
    cid = cell["cell_id"]
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": f'<cell id="{cid}">{"".join(metadata)}{cell["source"]}</cell id="{cid}">'}]
    for output in cell.get("outputs") or []:
        if not output:
            continue
        if output.get("text"):
            blocks.append({"type": "text", "text": f"\n{output['text']}"})
        image = output.get("image")
        if image:
            blocks.append({"type": "image", "base64": image["image_data"], "mime_type": image["media_type"]})
    return blocks


def map_notebook_cells_to_blocks(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`mapNotebookCellsToToolResult`'s content: every cell's blocks, adjacent text merged."""
    merged: list[dict[str, Any]] = []
    for block in (b for cell in cells for b in _cell_blocks(cell)):
        if merged and merged[-1]["type"] == "text" and block["type"] == "text":
            merged[-1] = {"type": "text", "text": merged[-1]["text"] + "\n" + block["text"]}
        else:
            merged.append(block)
    return merged


__all__ = [
    "LARGE_OUTPUT_THRESHOLD",
    "format_output",
    "get_max_output_length",
    "map_notebook_cells_to_blocks",
    "read_notebook",
]
