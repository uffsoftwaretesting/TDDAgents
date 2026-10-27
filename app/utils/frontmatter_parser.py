"""
Instruction-file reading for the prompt loaders (agents, skills, prompts).

The memory hierarchy itself (TDDAGENTS.md, rules, @include) lives in
`app/utils/claudemd.py`, the port of claude-code's `utils/claudemd.ts`.

Ported from Claude Code conventions:
- Pure reading from disk with optional comment stripping.
- Iterative HTML comment stripping before the model sees the text.
- Clean truncation if instruction files exceed safety limits.

Markdown prompt loader.

Replaces Jinja2 with plain Markdown, explicit variable substitution,
and disciplined frontmatter parsing as specified in §4 and §5 Part E4.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import yaml

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


logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}|\$\{([A-Za-z0-9_]+)\}|\{([A-Za-z0-9_]+)\}")


def render_prompt(
    template: str,
    vars: Mapping[str, Any] | None = None,
    strip_comments: bool = True,
) -> str:
    """
    Render prompt text by substituting {{VAR}}, ${VAR}, or {VAR} placeholders.

    Load-bearing acceptance rule (§4.1):
    Unresolved placeholders survive visibly in the text as {{VAR}} or {VAR}.
    They NEVER render as an empty string.
    """
    text = template
    if strip_comments:
        text = strip_html_comments(text)

    if not vars:
        return text

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2) or match.group(3)
        if key in vars:
            return str(vars[key])
        return match.group(0)

    return _VAR_RE.sub(_replace, text)


def parse_markdown_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """
    Parse YAML frontmatter delimited by leading '---' or '<!--'.

    Returns (frontmatter_dict, markdown_body).
    """
    stripped = content.lstrip()
    if stripped.startswith("---"):
        parts = stripped.split("---", 2)
        if len(parts) >= 3:
            raw_fm = parts[1]
            body = parts[2]
            try:
                data = yaml.safe_load(raw_fm)
                fm = data if isinstance(data, dict) else {}
            except Exception as e:  # noqa: BLE001 — malformed frontmatter is logged and ignored (§4.5)
                logger.warning("Failed to parse YAML frontmatter: %s", e)
                fm = {}
            return fm, body.strip()
    elif stripped.startswith("<!--"):
        parts = stripped.split("-->", 1)
        if len(parts) >= 2:
            raw_fm = parts[0][4:]
            body = parts[1]
            try:
                data = yaml.safe_load(raw_fm)
                fm = data if isinstance(data, dict) else {}
            except Exception as e:  # noqa: BLE001 — malformed frontmatter is logged and ignored (§4.5)
                logger.warning("Failed to parse HTML comment frontmatter: %s", e)
                fm = {}
            return fm, body.strip()

    return {}, content.strip()


__all__ = [
    "parse_markdown_frontmatter",
    "render_prompt",
]
