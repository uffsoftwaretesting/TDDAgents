"""
Opening a URL in the user's browser.

Ported from `reference/claude-code/src/utils/browser.ts` -> `validateUrl`, `openBrowser`.
The URL must parse and be http(s); `$BROWSER` wins, else `open` on macOS, `rundll32
url,OpenURL` on Windows and `xdg-open` elsewhere. The answer is whether the command exited 0;
any failure (a bad URL, a missing command) answers `False`, as upstream's `catch`.
Not ported: `openPath` (no caller here).
"""

from __future__ import annotations

import asyncio
import os
import sys
from urllib.parse import urlsplit


def validate_url(url: str) -> None:
    """`validateUrl`: raise unless `url` parses as an http(s) URL."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise ValueError(f"Invalid URL format: {url}") from None
    if not parsed.scheme:
        raise ValueError(f"Invalid URL format: {url}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL protocol: must use http:// or https://, got {parsed.scheme}:")


async def _exec_no_throw(command: str, args: list[str]) -> int:
    """`execFileNoThrow`'s exit code (`1` when the command cannot be started)."""
    try:
        process = await asyncio.create_subprocess_exec(
            command, *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
    except OSError:
        return 1
    return await process.wait()


async def open_browser(url: str) -> bool:
    """`openBrowser`."""
    try:
        validate_url(url)
    except ValueError:
        return False
    browser_env = os.environ.get("BROWSER")
    if sys.platform == "win32":
        if browser_env:
            return await _exec_no_throw(browser_env, [f'"{url}"']) == 0
        return await _exec_no_throw("rundll32", ["url,OpenURL", url]) == 0
    command = browser_env or ("open" if sys.platform == "darwin" else "xdg-open")
    return await _exec_no_throw(command, [url]) == 0


__all__ = ["open_browser", "validate_url"]
