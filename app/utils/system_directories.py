"""
Cross-platform system directories (home, desktop, documents, downloads).

Ported from `reference/claude-code/src/utils/systemDirectories.ts` -> `SystemDirectories`,
`getSystemDirectories`; and `reference/claude-code/src/utils/platform.ts` -> `Platform`,
`getPlatform` (memoized; `/proc/version` naming Microsoft or WSL is `wsl`).

`getPlatform` is ported here, beside its one plugin caller, because the repository's other
`get_platform` (`app/utils/shell/read_only_command_validation.py`) is a reduction that answers
only "windows or not" for the read-only command checks.
"""

from __future__ import annotations

import functools
import logging
import os
import sys
from collections.abc import Mapping
from typing import Literal, TypeAlias

logger = logging.getLogger("TDDOrchestrator.Utils")

#: `Platform`.
Platform: TypeAlias = Literal["macos", "windows", "wsl", "linux", "unknown"]
#: `SystemDirectories`: `HOME`, `DESKTOP`, `DOCUMENTS`, `DOWNLOADS`.
SystemDirectories: TypeAlias = dict[str, str]

_PROC_VERSION = "/proc/version"


def _detect_platform(sys_platform: str, proc_version_path: str = _PROC_VERSION) -> Platform:
    if sys_platform == "darwin":
        return "macos"
    if sys_platform == "win32":
        return "windows"
    if sys_platform.startswith("linux"):
        try:
            with open(proc_version_path, encoding="utf-8") as handle:
                proc_version = handle.read().lower()
            if "microsoft" in proc_version or "wsl" in proc_version:
                return "wsl"
        except OSError as exc:
            logger.debug("Error reading %s, assuming regular Linux: %s", proc_version_path, exc)
        return "linux"
    return "unknown"


@functools.cache
def get_platform() -> Platform:
    """`getPlatform` (memoized)."""
    return _detect_platform(sys.platform)


def get_system_directories(
    env: Mapping[str, str | None] | None = None,
    homedir: str | None = None,
    platform: Platform | None = None,
) -> SystemDirectories:
    """`getSystemDirectories`: XDG variables on Linux/WSL, `USERPROFILE` on Windows, defaults elsewhere."""
    resolved_platform = platform if platform is not None else get_platform()
    home_dir = homedir if homedir is not None else os.path.expanduser("~")
    environment: Mapping[str, str | None] = env if env is not None else os.environ
    defaults: SystemDirectories = {
        "HOME": home_dir,
        "DESKTOP": os.path.join(home_dir, "Desktop"),
        "DOCUMENTS": os.path.join(home_dir, "Documents"),
        "DOWNLOADS": os.path.join(home_dir, "Downloads"),
    }
    if resolved_platform == "windows":
        user_profile = environment.get("USERPROFILE") or home_dir
        return {
            "HOME": home_dir,
            "DESKTOP": os.path.join(user_profile, "Desktop"),
            "DOCUMENTS": os.path.join(user_profile, "Documents"),
            "DOWNLOADS": os.path.join(user_profile, "Downloads"),
        }
    if resolved_platform in ("linux", "wsl"):
        return {
            "HOME": home_dir,
            "DESKTOP": environment.get("XDG_DESKTOP_DIR") or defaults["DESKTOP"],
            "DOCUMENTS": environment.get("XDG_DOCUMENTS_DIR") or defaults["DOCUMENTS"],
            "DOWNLOADS": environment.get("XDG_DOWNLOAD_DIR") or defaults["DOWNLOADS"],
        }
    if resolved_platform == "unknown":
        logger.debug("Unknown platform detected, using default paths")
    return defaults


__all__ = ["Platform", "SystemDirectories", "get_platform", "get_system_directories"]
