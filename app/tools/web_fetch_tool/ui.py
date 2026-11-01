"""
How a WebFetch call is named and shown.

Ported from `reference/claude-code/src/tools/WebFetchTool/UI.tsx` -> `renderToolUseMessage` and
`WebFetchTool.ts` -> `userFacingName`.
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    return "Fetch"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    url = input.get("url")
    if not url:
        return None
    if verbose:
        prompt = input.get("prompt")
        return f'url: "{url}"' + (f', prompt: "{prompt}"' if prompt else "")
    return str(url)
