"""
Settings validation errors, grouped by file and drawn as a tree of their fields.

Ported from `reference/claude-code/src/components/ValidationErrorsList.tsx` -> `buildNestedTree`,
`ValidationErrorsList`: the files in alphabetical order (`(file not specified)` for an error without
one), each file's errors sorted by path and set into a tree by their dotted path (lodash `setWith`: a
later error at the same path replaces an earlier one; a trailing array index shows the invalid value
instead), drawn with `treeify` one column in, then each distinct suggestion (and its documentation
link) under the file. Lines instead of Ink boxes: the REPL prints them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.utils.settings.validation import ValidationError
from app.utils.treeify import treeify


#: What `parseInt(part, 10)` reads a number from (it is not `NaN`): optional blanks and sign, then a digit.
_PARSES_AS_INT = re.compile(r"\s*[+-]?\d")


def _display_value(value: Any) -> str:
    if isinstance(value, str):
        return f'"{value}"'
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _set_with(tree: dict[str, Any], path: str, value: str) -> None:
    """lodash `setWith(tree, path, value, Object)`: every missing (or non-object) step becomes an object."""
    parts = path.split(".")
    node = tree
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def build_nested_tree(errors: Sequence[ValidationError]) -> dict[str, Any]:
    """`buildNestedTree`."""
    tree: dict[str, Any] = {}
    for error in errors:
        if not error.path:
            tree[""] = error.message
            continue
        parts = error.path.split(".")
        modified = error.path
        if error.invalid_value is not None:
            shown: list[str] = []
            for i, part in enumerate(parts):
                if not part:
                    continue
                if _PARSES_AS_INT.match(part) and i == len(parts) - 1:
                    shown.append(_display_value(error.invalid_value))
                else:
                    shown.append(part)
            modified = ".".join(shown)
        _set_with(tree, modified, error.message)
    return tree


def render_validation_errors(errors: Sequence[ValidationError]) -> list[str]:
    """`ValidationErrorsList`'s lines."""
    by_file: dict[str, list[ValidationError]] = {}
    for error in errors:
        by_file.setdefault(error.file or "(file not specified)", []).append(error)
    lines: list[str] = []
    for file in sorted(by_file):
        file_errors = sorted(by_file[file], key=lambda e: (bool(e.path), e.path))
        lines.append(file)
        lines += [f" {line}" for line in treeify(build_nested_tree(file_errors)).split("\n")]
        pairs = list(dict.fromkeys((e.suggestion, e.doc_link) for e in file_errors if e.suggestion or e.doc_link))
        if pairs:
            lines.append("")
            for suggestion, doc_link in pairs:
                if suggestion:
                    lines.append(suggestion)
                if doc_link:
                    lines.append(f"Learn more: {doc_link}")
                lines.append("")
    return lines


__all__ = ["build_nested_tree", "render_validation_errors"]
