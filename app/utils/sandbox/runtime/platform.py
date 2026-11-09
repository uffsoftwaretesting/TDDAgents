"""
Platform detection for the sandbox runtime.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.46 `src/utils/platform.ts` -> `getPlatform`,
`getWslVersion`. `/proc/version` and `sys.platform` are parameters, so the WSL rules are testable.
"""

from __future__ import annotations

import re
import sys
from typing import Literal

Platform = Literal["macos", "linux", "windows", "unknown"]

PROC_VERSION = "/proc/version"


def get_wsl_version(platform: str = sys.platform, proc_version: str | None = None) -> str | None:
    """
    `getWslVersion`: `"2"` (or higher) for an explicit `WSL<n>` marker, `"1"` for a bare "Microsoft"
    kernel (WSL1's format), `None` off WSL or off Linux.
    """
    if platform != "linux":
        return None
    if proc_version is None:
        try:
            with open(PROC_VERSION, encoding="utf-8") as handle:
                proc_version = handle.read()
        except OSError:
            return None
    match = re.search(r"WSL(\d+)", proc_version, re.IGNORECASE)
    if match:
        return match.group(1)
    if "microsoft" in proc_version.lower():
        return "1"
    return None


def get_platform(platform: str = sys.platform) -> Platform:
    """`getPlatform`: WSL (any version) is `linux`; `isSupportedPlatform` rejects WSL1 separately."""
    if platform == "darwin":
        return "macos"
    if platform.startswith("linux"):
        return "linux"
    if platform == "win32":
        return "windows"
    return "unknown"


__all__ = ["PROC_VERSION", "Platform", "get_platform", "get_wsl_version"]
