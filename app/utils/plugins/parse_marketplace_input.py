"""
Parse a user's marketplace argument into a marketplace source.

Ported from `reference/claude-code/src/utils/plugins/parseMarketplaceInput.ts` -> `parseMarketplaceInput`.

Recognized, in order: git SSH URLs (`user@host:path[.git][#ref]`), http(s) URLs (`.git` or
`/_git/` -> `git`; a github.com repository URL -> `git` with `.git` appended; anything else ->
`url`), local paths (`./`, `../`, `/`, `~`; a `.json` file or a directory), and GitHub shorthand
(`owner/repo`, with `#ref` or `@ref`). Anything else is `None`. The Windows-only path forms are
not recognized on other platforms, as upstream.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import sys
from typing import Any
from urllib.parse import urlsplit

_SSH = re.compile(r"^([a-zA-Z0-9._-]+@[^:]+:.+?(?:\.git)?)(#(.+))?\Z", re.ASCII)
_FRAGMENT = re.compile(r"^([^#]+)(#(.+))?\Z", re.ASCII)
_GITHUB_PATH = re.compile(r"^/([^/]+/[^/]+?)(/|\.git|$)")
_SHORTHAND = re.compile(r"^([^#@]+)(?:[#@](.+))?\Z", re.ASCII)
_WINDOWS_DRIVE = re.compile(r"^[a-zA-Z]:[/\\]")


def _with_ref(source: dict[str, Any], ref: str | None) -> dict[str, Any]:
    if ref:
        source["ref"] = ref
    return source


async def parse_marketplace_input(value: str) -> dict[str, Any] | None:
    """`parseMarketplaceInput`: a `MarketplaceSource`, `{"error": ...}`, or `None` if unrecognized."""
    trimmed = value.strip()

    ssh = _SSH.match(trimmed)
    if ssh and ssh.group(1):
        return _with_ref({"source": "git", "url": ssh.group(1)}, ssh.group(3))

    if trimmed.startswith(("http://", "https://")):
        fragment = _FRAGMENT.match(trimmed)
        url_without_fragment = (fragment.group(1) if fragment else None) or trimmed
        ref = fragment.group(3) if fragment else None
        if url_without_fragment.endswith(".git") or "/_git/" in url_without_fragment:
            return _with_ref({"source": "git", "url": url_without_fragment}, ref)
        try:
            parsed = urlsplit(url_without_fragment)
            hostname = (parsed.hostname or "").lower()
            if not hostname:
                raise ValueError("no host")
        except ValueError:
            return {"source": "url", "url": url_without_fragment}
        if hostname in ("github.com", "www.github.com"):
            match = _GITHUB_PATH.match(parsed.path or "/")
            if match and match.group(1):
                git_url = url_without_fragment
                if not git_url.endswith(".git"):
                    git_url = f"{git_url}.git"
                return _with_ref({"source": "git", "url": git_url}, ref)
        return {"source": "url", "url": url_without_fragment}

    is_windows_path = sys.platform == "win32" and (
        trimmed.startswith((".\\", "..\\")) or _WINDOWS_DRIVE.match(trimmed) is not None
    )
    if trimmed.startswith(("./", "../", "/", "~")) or is_windows_path:
        expanded = os.path.expanduser("~") + trimmed[1:] if trimmed.startswith("~") else trimmed
        resolved = os.path.abspath(expanded)
        try:
            info = os.stat(resolved)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return {"error": f"Path does not exist: {resolved}"}
            code = errno.errorcode.get(exc.errno or 0, str(exc))
            return {"error": f"Cannot access path: {resolved} ({code})"}
        if stat.S_ISREG(info.st_mode):
            if resolved.endswith(".json"):
                return {"source": "file", "path": resolved}
            return {"error": f"File path must point to a .json file (marketplace.json), but got: {resolved}"}
        if stat.S_ISDIR(info.st_mode):
            return {"source": "directory", "path": resolved}
        return {"error": f"Path is neither a file nor a directory: {resolved}"}

    if "/" in trimmed and not trimmed.startswith("@"):
        if ":" in trimmed:
            return None
        shorthand = _SHORTHAND.match(trimmed)
        repo = (shorthand.group(1) if shorthand else None) or trimmed
        ref = shorthand.group(2) if shorthand else None
        return _with_ref({"source": "github", "repo": repo}, ref)

    return None


__all__ = ["parse_marketplace_input"]
