"""
The bordered monospace box an `AskUserQuestion` option's `preview` is shown in.

Ported from `reference/claude-code/src/components/permissions/AskUserQuestionPermissionRequest/`:

- `PreviewBox.tsx` -> `BOX_CHARS`, `PreviewBox` / `PreviewBoxBody`: the content's lines in a box
  at least `minWidth` (40) wide, at most `maxWidth` (the terminal's width less 4), each line cut to
  the inner width; past `maxLines` (20 by default) the rest is replaced by the
  `─── ✂ ─── N lines hidden` bar.
- `QuestionView.tsx` -> `hasAnyPreview`: a question shows previews when it is single-select and
  one of its options has one.

TDDAgents' REPL dialogs are lines, not a focusable Ink list, so where upstream's
`PreviewQuestionView` shows the focused option's preview beside the list (`No preview available`
for an option without one), the REPL prints every option's preview under that option
(`app/screens/repl.py::render_question`). Not ported: `applyMarkdown` and syntax highlighting
(the preview is printed as written), `minHeight` padding (a line-based dialog has no panel height to
match), and `stringWidth`: widths are counted in characters.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from typing import Any

#: `BOX_CHARS`.
TOP_LEFT, TOP_RIGHT, BOTTOM_LEFT, BOTTOM_RIGHT = "┌", "┐", "└", "┘"
HORIZONTAL, VERTICAL, TEE_LEFT, TEE_RIGHT = "─", "│", "├", "┤"

#: `PreviewBoxBody`'s `maxLines ?? 20` and `minWidth = 40`.
DEFAULT_MAX_LINES = 20
DEFAULT_MIN_WIDTH = 40


def has_any_preview(question: Mapping[str, Any]) -> bool:
    """`hasAnyPreview`: single-select, and some option carries a `preview`."""
    return not question.get("multiSelect") and any(
        isinstance(option, Mapping) and option.get("preview") for option in question.get("options") or ())


def preview_box(content: str, max_lines: int | None = None, min_width: int = DEFAULT_MIN_WIDTH,
                max_width: int | None = None) -> list[str]:
    """`PreviewBoxBody`: the box's lines, borders included."""
    effective_max_width = max_width if max_width is not None else shutil.get_terminal_size().columns - 4
    effective_max_lines = max_lines if max_lines is not None else DEFAULT_MAX_LINES
    content_lines = content.split("\n")
    is_truncated = len(content_lines) > effective_max_lines
    lines = content_lines[:effective_max_lines] if is_truncated else content_lines
    content_width = max(min_width, *(len(line) for line in lines))
    box_width = min(content_width + 4, effective_max_width)
    inner_width = box_width - 4
    out = [f"{TOP_LEFT}{HORIZONTAL * (box_width - 2)}{TOP_RIGHT}"]
    for line in lines:
        shown = line[:inner_width]
        out.append(f"{VERTICAL} {shown}{' ' * max(0, inner_width - len(shown))} {VERTICAL}")
    if is_truncated:
        hidden = len(content_lines) - effective_max_lines
        label = f"{HORIZONTAL * 3} ✂ {HORIZONTAL * 3} {hidden} lines hidden "
        out.append(f"{TEE_LEFT}{label}{HORIZONTAL * max(0, box_width - 2 - len(label))}{TEE_RIGHT}")
    out.append(f"{BOTTOM_LEFT}{HORIZONTAL * (box_width - 2)}{BOTTOM_RIGHT}")
    return out


__all__ = [
    "BOTTOM_LEFT",
    "BOTTOM_RIGHT",
    "DEFAULT_MAX_LINES",
    "DEFAULT_MIN_WIDTH",
    "HORIZONTAL",
    "TEE_LEFT",
    "TEE_RIGHT",
    "TOP_LEFT",
    "TOP_RIGHT",
    "VERTICAL",
    "has_any_preview",
    "preview_box",
]
