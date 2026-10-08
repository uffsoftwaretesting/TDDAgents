"""
Instruction-file reading for the prompt loaders (agents, skills, prompts).

The memory hierarchy itself (TDDAGENTS.md, rules, @include) lives in
`app/loop/context/memory.py`, the port of claude-code's `utils/claudemd.ts`.

Ported from Claude Code conventions:
- Pure reading from disk with optional comment stripping.
- Iterative HTML comment stripping before the model sees the text.
- Clean truncation if instruction files exceed safety limits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# Matches innermost HTML comment that does not contain another opening <!--
_INNER_HTML_COMMENT_RE = re.compile(r"<!--((?!<!--)[\s\S])*?-->")


def strip_html_comments(text: str) -> str:
    """
    Strip HTML comments from markdown iteratively until stable.

    Innermost comments are resolved first so nested/adjacent comments
    are cleanly and stably removed.
    """
    prev = None
    curr = text
    while prev != curr:
        prev = curr
        curr = _INNER_HTML_COMMENT_RE.sub("", curr)
    return curr


@dataclass(frozen=True, slots=True)
class InstructionFile:
    """Loaded project instruction or conventions file."""

    path: str
    content: str
    truncated: bool = False
    original_bytes: int = 0


def load_instruction_file(
    file_path: Path | str,
    max_bytes: int = 50_000,
    strip_comments: bool = True,
) -> InstructionFile | None:
    """
    Load an instruction file from disk.

    Returns None if file does not exist or is not a regular file.
    Truncates and adds a notice if file size exceeds max_bytes.
    """
    path = Path(file_path)
    if not path.is_file():
        return None

    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return None

    original_size = len(raw_bytes)
    truncated = False

    if original_size > max_bytes:
        truncated = True
        raw_bytes = raw_bytes[:max_bytes]
        text = raw_bytes.decode("utf-8", errors="replace")
        if strip_comments:
            text = strip_html_comments(text)
        text += f"\n\n[Truncated: original file size {original_size} bytes exceeded limit {max_bytes}]"
    else:
        text = raw_bytes.decode("utf-8", errors="replace")
        if strip_comments:
            text = strip_html_comments(text)

    return InstructionFile(
        path=str(path),
        content=text.strip(),
        truncated=truncated,
        original_bytes=original_size,
    )
