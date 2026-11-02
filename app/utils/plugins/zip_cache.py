"""
The plugin zip cache switch.

Ported from `reference/claude-code/src/utils/plugins/zipCache.ts` -> `isPluginZipCacheEnabled`.

Stage 1 ports only the switch (`CLAUDE_CODE_PLUGIN_USE_ZIP_CACHE` is `TDDAGENTS_PLUGIN_USE_ZIP_CACHE`).
The zip operations (`convertDirectoryToZipInPlace`, `extractZipToDirectory`,
`getSessionPluginCachePath`, ...) belong to stage 2; until then `convert_directory_to_zip_in_place`
raises `NotImplementedError`, and `require_zip_cache_port` fails an install before it writes
anything, so a run with the switch on fails loudly instead of caching a directory where a zip
is expected.
"""

from __future__ import annotations

import os

from app.utils.claudemd import is_env_truthy

PLUGIN_USE_ZIP_CACHE_ENV = "TDDAGENTS_PLUGIN_USE_ZIP_CACHE"


def is_plugin_zip_cache_enabled() -> bool:
    """`isPluginZipCacheEnabled`."""
    return is_env_truthy(os.environ.get(PLUGIN_USE_ZIP_CACHE_ENV))


def require_zip_cache_port() -> None:
    """
    Raise when the zip cache is switched on: its operations are stage 2. Called before an
    install writes anything, so a run with the switch on leaves no directory behind that a
    later run without it would reuse.
    """
    if is_plugin_zip_cache_enabled():
        raise NotImplementedError(f"The plugin zip cache ({PLUGIN_USE_ZIP_CACHE_ENV}) is not ported yet (stage 2)")


async def convert_directory_to_zip_in_place(directory: str, zip_path: str) -> None:
    """`convertDirectoryToZipInPlace`: not ported yet (stage 2)."""
    raise NotImplementedError(
        f"The plugin zip cache ({PLUGIN_USE_ZIP_CACHE_ENV}) is not ported yet (stage 2): "
        f"cannot convert {directory} to {zip_path}"
    )


__all__ = [
    "PLUGIN_USE_ZIP_CACHE_ENV",
    "convert_directory_to_zip_in_place",
    "is_plugin_zip_cache_enabled",
    "require_zip_cache_port",
]
