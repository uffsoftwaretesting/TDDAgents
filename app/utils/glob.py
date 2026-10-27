"""
Ported from `reference/claude-code/src/utils/glob.ts` -> `extractGlobBaseDirectory`.
"""

from __future__ import annotations

import re


def extract_glob_base_directory(pattern: str) -> tuple[str, str]:
    """
    `src/utils/glob.ts` -> `extractGlobBaseDirectory`: the static directory before the first
    glob character, and the remaining pattern. rg's `--glob` only takes relative patterns.
    """
    match = re.search(r"[*?[{]", pattern)
    if match is None:
        head, _, tail = pattern.rpartition("/")
        return (head or ("/" if pattern.startswith("/") else ".")), tail
    static_prefix = pattern[:match.start()]
    last_sep = static_prefix.rfind("/")
    if last_sep == -1:
        return "", pattern
    base = static_prefix[:last_sep] or "/"
    return base, pattern[last_sep + 1:]
