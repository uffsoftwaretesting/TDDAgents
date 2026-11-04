"""
A nested mapping drawn as a tree.

Ported from `reference/claude-code/src/utils/treeify.ts` -> `TreeNode`, `treeify` (the
`showValues` rendering, without the theme colours: the REPL prints plain lines). Keys that are array
indices come first, in numeric order, as a JS object enumerates them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: `figures.lineUpDownRight`, `figures.lineUpRight`, `figures.lineVertical`.
BRANCH, LAST_BRANCH, LINE, EMPTY = "├", "└", "│", " "

TreeNode = Mapping[str, Any]


def _is_index(key: str) -> bool:
    return key.isdigit() and (key == "0" or not key.startswith("0"))


def js_keys(node: Mapping[str, Any]) -> list[str]:
    """`Object.keys`: the integer-like keys ascending, then the rest in insertion order."""
    keys = [str(k) for k in node]
    return sorted((k for k in keys if _is_index(k)), key=int) + [k for k in keys if not _is_index(k)]


def treeify(tree: TreeNode) -> str:
    """`treeify(obj, {showValues: true})`."""
    keys = js_keys(tree)
    if not keys:
        return "(empty)"
    if len(keys) == 1 and keys[0].strip() == "" and isinstance(tree[keys[0]], str):
        return f"{LAST_BRANCH} {tree[keys[0]]}"
    lines: list[str] = []

    def grow(node: Mapping[str, Any], prefix: str, depth: int) -> None:
        node_keys = js_keys(node)
        for index, key in enumerate(node_keys):
            value = node[key]
            is_last = index == len(node_keys) - 1
            node_prefix = "" if depth == 0 and index == 0 else prefix
            line = node_prefix + (LAST_BRANCH if is_last else BRANCH) + (f" {key}" if key.strip() else "")
            separator = ": " if key.strip() else " "
            if isinstance(value, Mapping):
                lines.append(line)
                grow(value, node_prefix + (EMPTY if is_last else LINE) + " ", depth + 1)
            elif isinstance(value, list):
                lines.append(f"{line}{separator}[Array({len(value)})]")
            else:
                lines.append(f"{line}{separator}{value}")

    grow(tree, "", 0)
    return "\n".join(lines)


__all__ = ["BRANCH", "EMPTY", "LAST_BRANCH", "LINE", "TreeNode", "js_keys", "treeify"]
