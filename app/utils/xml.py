"""
XML escaping for text placed inside a tag.

Ported from `reference/claude-code/src/utils/xml.ts` -> `escapeXml`.
"""

from __future__ import annotations


def escape_xml(text: str) -> str:
    """`escapeXml`: `&`, `<` and `>`."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
