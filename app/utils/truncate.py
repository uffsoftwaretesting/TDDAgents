"""
Width-aware truncation and wrapping, in terminal columns, on grapheme boundaries.

Ported from `reference/claude-code/src/utils/truncate.ts` -> `truncatePathMiddle`,
`truncateToWidth`, `truncateStartToWidth`, `truncateToWidthNoEllipsis`, `truncate`, `wrapText`.
"""

from __future__ import annotations

from app.ink.string_width import string_width
from app.utils.intl import graphemes

ELLIPSIS = "…"


def truncate_to_width(text: str, max_width: int) -> str:
    """`truncateToWidth`: the head that fits, then `…`."""
    if string_width(text) <= max_width:
        return text
    if max_width <= 1:
        return ELLIPSIS
    width, result = 0, ""
    for segment in graphemes(text):
        seg_width = string_width(segment)
        if width + seg_width > max_width - 1:
            break
        result += segment
        width += seg_width
    return result + ELLIPSIS


def truncate_start_to_width(text: str, max_width: int) -> str:
    """`truncateStartToWidth`: `…`, then the tail that fits."""
    if string_width(text) <= max_width:
        return text
    if max_width <= 1:
        return ELLIPSIS
    segments = graphemes(text)
    width, start = 0, len(segments)
    for index in range(len(segments) - 1, -1, -1):
        seg_width = string_width(segments[index])
        if width + seg_width > max_width - 1:
            break
        width += seg_width
        start = index
    return ELLIPSIS + "".join(segments[start:])


def truncate_to_width_no_ellipsis(text: str, max_width: int) -> str:
    """`truncateToWidthNoEllipsis`: the head that fits, nothing appended."""
    if string_width(text) <= max_width:
        return text
    if max_width <= 0:
        return ""
    width, result = 0, ""
    for segment in graphemes(text):
        seg_width = string_width(segment)
        if width + seg_width > max_width:
            break
        result += segment
        width += seg_width
    return result


def truncate_path_middle(path: str, max_length: int) -> str:
    """`truncatePathMiddle`: keep the file name, cut the directory in the middle."""
    if string_width(path) <= max_length:
        return path
    if max_length <= 0:
        return ELLIPSIS
    if max_length < 5:
        return truncate_to_width(path, max_length)
    last_slash = path.rfind("/")
    filename = path[last_slash:] if last_slash >= 0 else path
    directory = path[:last_slash] if last_slash >= 0 else ""
    filename_width = string_width(filename)
    if filename_width >= max_length - 1:
        return truncate_start_to_width(path, max_length)
    available = max_length - 1 - filename_width
    return truncate_to_width_no_ellipsis(directory, available) + ELLIPSIS + filename


def truncate(text: str, max_width: int, single_line: bool = False) -> str:
    """`truncate`: to `max_width` columns; with `single_line`, also at the first newline."""
    result = text
    if single_line:
        newline = text.find("\n")
        if newline != -1:
            result = text[:newline]
            if string_width(result) + 1 > max_width:
                return truncate_to_width(result, max_width)
            return result + ELLIPSIS
    if string_width(result) <= max_width:
        return result
    return truncate_to_width(result, max_width)


def wrap_text(text: str, width: int) -> list[str]:
    """`wrapText`: lines of at most `width` columns, broken between graphemes."""
    lines: list[str] = []
    current, current_width = "", 0
    for segment in graphemes(text):
        seg_width = string_width(segment)
        if current_width + seg_width <= width:
            current += segment
            current_width += seg_width
        else:
            if current:
                lines.append(current)
            current, current_width = segment, seg_width
    if current:
        lines.append(current)
    return lines


__all__ = [
    "ELLIPSIS",
    "truncate",
    "truncate_path_middle",
    "truncate_start_to_width",
    "truncate_to_width",
    "truncate_to_width_no_ellipsis",
    "wrap_text",
]
