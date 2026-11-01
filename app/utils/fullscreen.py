"""
Whether the terminal runs in "fullscreen" (no-flicker) mode.

Ported from `reference/claude-code/src/utils/fullscreen.ts` -> `isFullscreenEnvEnabled`, with
`CLAUDE_CODE_NO_FLICKER` as `TDDAGENTS_NO_FLICKER`: an explicit falsy value turns it off, a
truthy one on, and otherwise it is off (upstream turns it on only for Anthropic employees). In
TDDAgents it gates the read/search collapse's fullscreen-only branches (bash commands counted,
git operations surfaced, the shell-progress suffix). The tmux `-CC` auto-disable only matters
for the employee default, so it has nothing to decide here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

NO_FLICKER_ENV = "TDDAGENTS_NO_FLICKER"

_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def is_fullscreen_env_enabled(env: Mapping[str, str] | None = None) -> bool:
    """`isFullscreenEnvEnabled`."""
    value = (os.environ if env is None else env).get(NO_FLICKER_ENV)
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in _FALSY:
        return False
    return normalized in _TRUTHY


__all__ = ["NO_FLICKER_ENV", "is_fullscreen_env_enabled"]
