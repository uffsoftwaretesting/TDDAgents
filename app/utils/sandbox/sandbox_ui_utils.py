"""
UI helpers for sandbox violations.

Ported from `reference/claude-code/src/utils/sandbox/sandbox-ui-utils.ts` -> `removeSandboxViolationTags`.
"""

from __future__ import annotations

import re


def remove_sandbox_violation_tags(text: str) -> str:
    """`removeSandboxViolationTags`: the `<sandbox_violations>` blocks, for display."""
    return re.sub(r"<sandbox_violations>[\s\S]*?</sandbox_violations>", "", text)


__all__ = ["remove_sandbox_violation_tags"]
