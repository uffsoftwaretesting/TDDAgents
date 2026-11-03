"""
The `--debug [filter]` category filter.

Ported from `reference/claude-code/src/utils/debugFilter.ts` -> `DebugFilter`, `parseDebugFilter`,
`extractDebugCategories`, `shouldShowDebugCategories`, `shouldShowDebugMessage`.

A filter is a comma-separated list of categories: `api,hooks` shows only those, `!1p,!file` hides
those. Mixing both kinds is upstream's error case, which shows everything. A message's categories
come from its own text (`hooks: …`, `[HOOKS] …`, `MCP server "name": …`), lowercased.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MCP_SERVER = re.compile(r"""^MCP server ["']([^"']+)["']""")
_PREFIX = re.compile(r"^([^:\[]+):")
_BRACKET = re.compile(r"^\[([^\]]+)]")
_SECONDARY = re.compile(r":\s*([^:]+?)(?:\s+(?:type|mode|status|event))?:")
#: `extractDebugCategories`' cap on a secondary category's length.
MAX_SECONDARY_CATEGORY_LENGTH = 30


@dataclass(frozen=True, slots=True)
class DebugFilter:
    """`DebugFilter`."""

    include: tuple[str, ...]
    exclude: tuple[str, ...]
    is_exclusive: bool


def parse_debug_filter(filter_string: str | None) -> DebugFilter | None:
    """`parseDebugFilter`: `None` for no filter, an empty one, or one mixing `x` and `!x`."""
    if not filter_string or filter_string.strip() == "":
        return None
    filters = [f.strip() for f in filter_string.split(",") if f.strip()]
    if not filters:
        return None
    has_exclusive = any(f.startswith("!") for f in filters)
    has_inclusive = any(not f.startswith("!") for f in filters)
    if has_exclusive and has_inclusive:
        return None
    clean = tuple(re.sub(r"^!", "", f).lower() for f in filters)
    return DebugFilter(
        include=() if has_exclusive else clean,
        exclude=clean if has_exclusive else (),
        is_exclusive=has_exclusive,
    )


def extract_debug_categories(message: str) -> list[str]:
    """`extractDebugCategories`: the message's categories, lowercased, without duplicates."""
    categories: list[str] = []
    mcp = _MCP_SERVER.match(message)
    if mcp and mcp.group(1):
        categories.append("mcp")
        categories.append(mcp.group(1).lower())
    else:
        prefix = _PREFIX.match(message)
        if prefix and prefix.group(1):
            categories.append(prefix.group(1).strip().lower())
    bracket = _BRACKET.match(message)
    if bracket and bracket.group(1):
        categories.append(bracket.group(1).strip().lower())
    if "1p event:" in message.lower():
        categories.append("1p")
    secondary = _SECONDARY.search(message)
    if secondary and secondary.group(1):
        candidate = secondary.group(1).strip().lower()
        if len(candidate) < MAX_SECONDARY_CATEGORY_LENGTH and " " not in candidate:
            categories.append(candidate)
    return list(dict.fromkeys(categories))


def should_show_debug_categories(categories: list[str], debug_filter: DebugFilter | None) -> bool:
    """`shouldShowDebugCategories`: an uncategorized message passes no filter."""
    if debug_filter is None:
        return True
    if not categories:
        return False
    if debug_filter.is_exclusive:
        return not any(category in debug_filter.exclude for category in categories)
    return any(category in debug_filter.include for category in categories)


def should_show_debug_message(message: str, debug_filter: DebugFilter | None) -> bool:
    """`shouldShowDebugMessage`."""
    if debug_filter is None:
        return True
    return should_show_debug_categories(extract_debug_categories(message), debug_filter)


__all__ = [
    "DebugFilter",
    "extract_debug_categories",
    "parse_debug_filter",
    "should_show_debug_categories",
    "should_show_debug_message",
]
