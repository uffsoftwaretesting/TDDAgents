"""
Listing a memory directory: each file's frontmatter, newest first.

Ported from `reference/claude-code/src/memdir/memoryScan.ts` -> `MemoryHeader`, `scanMemoryFiles`,
`formatMemoryManifest`. Every `*.md` file below the directory except `MEMORY.md` (the index),
read for its first `FRONTMATTER_MAX_LINES` lines; files that cannot be read are skipped; at most
`MAX_MEMORY_FILES`, newest first. Synchronous here: the caller already runs it off the loop.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path

from app.memdir.memory_types import parse_memory_type
from app.utils.frontmatter_parser import parse_markdown_frontmatter

#: `MAX_MEMORY_FILES` / `FRONTMATTER_MAX_LINES`.
MAX_MEMORY_FILES = 200
FRONTMATTER_MAX_LINES = 30
#: `ENTRYPOINT_NAME`, the index the scan leaves out.
INDEX_NAME = "MEMORY.md"


@dataclass(frozen=True, slots=True)
class MemoryHeader:
    """`MemoryHeader`."""

    filename: str
    file_path: str
    mtime_ms: float
    description: str | None
    type: str | None


def _head(path: Path, max_lines: int) -> str:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return "".join(line for _, line in zip(range(max_lines), handle, strict=False))


def scan_memory_files(memory_dir: str) -> list[MemoryHeader]:
    """`scanMemoryFiles`: the headers, newest first, capped; `[]` when the directory is missing."""
    root = Path(memory_dir)
    headers: list[MemoryHeader] = []
    try:
        candidates = sorted(p for p in root.rglob("*.md") if p.name != INDEX_NAME and p.is_file())
    except OSError:
        return []
    for path in candidates:
        try:
            frontmatter, _ = parse_markdown_frontmatter(_head(path, FRONTMATTER_MAX_LINES))
            mtime_ms = os.path.getmtime(path) * 1000
        except (OSError, ValueError):
            continue
        description = frontmatter.get("description")
        headers.append(MemoryHeader(
            filename=str(path.relative_to(root)), file_path=str(path), mtime_ms=mtime_ms,
            description=str(description) if description else None, type=parse_memory_type(frontmatter.get("type")),
        ))
    headers.sort(key=lambda h: h.mtime_ms, reverse=True)
    return headers[:MAX_MEMORY_FILES]


def _iso(mtime_ms: float) -> str:
    """`new Date(ms).toISOString()`."""
    moment = datetime.datetime.fromtimestamp(mtime_ms / 1000, tz=datetime.UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def format_memory_manifest(memories: list[MemoryHeader]) -> str:
    """`formatMemoryManifest`: `- [type] file (timestamp): description`, one per line."""
    lines = []
    for memory in memories:
        tag = f"[{memory.type}] " if memory.type else ""
        stamp = _iso(memory.mtime_ms)
        lines.append(f"- {tag}{memory.filename} ({stamp}): {memory.description}" if memory.description
                     else f"- {tag}{memory.filename} ({stamp})")
    return "\n".join(lines)
