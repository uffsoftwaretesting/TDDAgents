"""
The plugin zip cache: plugins stored as ZIP archives on a mounted directory, extracted into a
session-local temporary directory when loaded.

Ported from `reference/claude-code/src/utils/plugins/zipCache.ts` -> `isPluginZipCacheEnabled`,
`getPluginZipCachePath`, `getZipCacheKnownMarketplacesPath`, `getZipCacheInstalledPluginsPath`,
`getZipCacheMarketplacesDir`, `getZipCachePluginsDir`, `getSessionPluginCachePath`,
`cleanupSessionPluginCache`, `resetSessionPluginCache`, `atomicWriteToZipCache`,
`createZipFromDirectory`, `collectFilesForZip`, `extractZipToDirectory`,
`convertDirectoryToZipInPlace`, `getMarketplaceJsonRelativePath`,
`isMarketplaceSourceSupportedByZipCache`.

Product substitutions: `CLAUDE_CODE_PLUGIN_USE_ZIP_CACHE` / `CLAUDE_CODE_PLUGIN_CACHE_DIR` are
`TDDAGENTS_PLUGIN_USE_ZIP_CACHE` / `TDDAGENTS_PLUGIN_CACHE_DIR`; the session directory is
`tddagents-plugin-session-<hex>` (upstream `claude-plugin-session-<hex>`).

Upstream zips with fflate (`zipSync`, level 6, each entry `[data, {os: 3, attrs: mode << 16}]`);
here `zipfile` with `ZIP_DEFLATED` level 6 and `create_system = 3` writes the same Unix
`external_attr`, so `parse_zip_modes` reads the modes back on extraction. Upstream's
`lstat`-then-`stat` walk is kept: `.git` is skipped, a symlinked directory is skipped, a
symlinked file is stored with its target's content and mode, and a directory already visited
(by device and inode, unless both are 0) is skipped as a cycle.
"""

from __future__ import annotations

import io
import logging
import os
import re
import secrets
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Mapping
from typing import Any

from app.tools.bash_tool.path_validation import expand_tilde
from app.utils.claudemd import is_env_truthy
from app.utils.dxt.zip import parse_zip_modes, unzip_file
from app.utils.plugins.plugin_directories import PLUGIN_CACHE_DIR_ENV

logger = logging.getLogger("TDDOrchestrator.Plugins")

PLUGIN_USE_ZIP_CACHE_ENV = "TDDAGENTS_PLUGIN_USE_ZIP_CACHE"
#: The session cache directory's prefix (upstream `claude-plugin-session-`).
SESSION_PLUGIN_CACHE_PREFIX = "tddagents-plugin-session-"
#: fflate's `zipSync(files, { level: 6 })`.
ZIP_COMPRESSION_LEVEL = 6

_session_plugin_cache_path: str | None = None


def is_plugin_zip_cache_enabled() -> bool:
    """`isPluginZipCacheEnabled`."""
    return is_env_truthy(os.environ.get(PLUGIN_USE_ZIP_CACHE_ENV))


def get_plugin_zip_cache_path() -> str | None:
    """`getPluginZipCachePath`: the cache directory (tilde-expanded), `None` when the zip cache is off or unset."""
    if not is_plugin_zip_cache_enabled():
        return None
    directory = os.environ.get(PLUGIN_CACHE_DIR_ENV)
    return expand_tilde(directory) if directory else None


def _require_zip_cache_path() -> str:
    cache_path = get_plugin_zip_cache_path()
    if not cache_path:
        raise RuntimeError("Plugin zip cache is not enabled")
    return cache_path


def get_zip_cache_known_marketplaces_path() -> str:
    """`getZipCacheKnownMarketplacesPath`."""
    return os.path.join(_require_zip_cache_path(), "known_marketplaces.json")


def get_zip_cache_installed_plugins_path() -> str:
    """`getZipCacheInstalledPluginsPath`."""
    return os.path.join(_require_zip_cache_path(), "installed_plugins.json")


def get_zip_cache_marketplaces_dir() -> str:
    """`getZipCacheMarketplacesDir`."""
    return os.path.join(_require_zip_cache_path(), "marketplaces")


def get_zip_cache_plugins_dir() -> str:
    """`getZipCachePluginsDir`."""
    return os.path.join(_require_zip_cache_path(), "plugins")


async def get_session_plugin_cache_path() -> str:
    """`getSessionPluginCachePath`: created once per process, under the system temp directory."""
    global _session_plugin_cache_path
    if _session_plugin_cache_path:
        return _session_plugin_cache_path
    directory = os.path.join(tempfile.gettempdir(), f"{SESSION_PLUGIN_CACHE_PREFIX}{secrets.token_hex(8)}")
    os.makedirs(directory, exist_ok=True)
    _session_plugin_cache_path = directory
    logger.debug("Created session plugin cache at %s", directory)
    return directory


async def cleanup_session_plugin_cache() -> None:
    """`cleanupSessionPluginCache`: remove the session directory; failures are logged."""
    global _session_plugin_cache_path
    if not _session_plugin_cache_path:
        return
    try:
        shutil.rmtree(_session_plugin_cache_path, ignore_errors=False)
        logger.debug("Cleaned up session plugin cache at %s", _session_plugin_cache_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Failed to clean up session plugin cache: %s", exc)
    finally:
        _session_plugin_cache_path = None


def reset_session_plugin_cache() -> None:
    """`resetSessionPluginCache` (for tests)."""
    global _session_plugin_cache_path
    _session_plugin_cache_path = None


async def atomic_write_to_zip_cache(target_path: str, data: str | bytes) -> None:
    """`atomicWriteToZipCache`: write a hidden temporary file beside the target, then rename it."""
    directory = os.path.dirname(target_path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = os.path.join(directory, f".{os.path.basename(target_path)}.tmp.{secrets.token_hex(4)}")
    try:
        if isinstance(data, str):
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(data)
        else:
            with open(tmp_path, "wb") as handle:
                handle.write(data)
        os.replace(tmp_path, target_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


async def create_zip_from_directory(source_dir: str) -> bytes:
    """`createZipFromDirectory`: the directory as ZIP bytes, symlinked files resolved, Unix modes kept."""
    files: dict[str, tuple[bytes, int]] = {}
    visited: set[str] = set()
    await _collect_files_for_zip(source_dir, "", files, visited)
    buffer = _zip_entries(files)
    logger.debug("Created ZIP from %s: %d files, %d bytes", source_dir, len(files), len(buffer))
    return buffer


def _zip_entries(files: Mapping[str, tuple[bytes, int]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=ZIP_COMPRESSION_LEVEL) as archive:
        for rel_path, (content, mode) in files.items():
            info = zipfile.ZipInfo(rel_path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=ZIP_COMPRESSION_LEVEL)
    return out.getvalue()


async def _collect_files_for_zip(
    base_dir: str, relative_path: str, files: dict[str, tuple[bytes, int]], visited: set[str]
) -> None:
    """`collectFilesForZip`."""
    current_dir = os.path.join(base_dir, relative_path) if relative_path else base_dir
    try:
        entries = os.listdir(current_dir)
    except OSError:
        return
    try:
        dir_stat = os.stat(current_dir)
    except OSError:
        return
    if dir_stat.st_dev != 0 or dir_stat.st_ino != 0:
        key = f"{dir_stat.st_dev}:{dir_stat.st_ino}"
        if key in visited:
            logger.debug("Skipping symlink cycle at %s", current_dir)
            return
        visited.add(key)
    for entry in entries:
        if entry == ".git":
            continue
        full_path = os.path.join(current_dir, entry)
        rel_path = f"{relative_path}/{entry}" if relative_path else entry
        try:
            file_stat: Any = os.lstat(full_path)
        except OSError:
            continue
        if stat.S_ISLNK(file_stat.st_mode):
            try:
                target_stat = os.stat(full_path)
            except OSError:
                continue
            if stat.S_ISDIR(target_stat.st_mode):
                continue
            file_stat = target_stat
        if stat.S_ISDIR(file_stat.st_mode):
            await _collect_files_for_zip(base_dir, rel_path, files, visited)
        elif stat.S_ISREG(file_stat.st_mode):
            try:
                with open(full_path, "rb") as handle:
                    content = handle.read()
            except OSError as exc:
                logger.debug("Failed to read file for zip: %s: %s", rel_path, exc)
                continue
            files[rel_path] = (content, file_stat.st_mode & 0xFFFF)


async def extract_zip_to_directory(zip_path: str, target_dir: str) -> None:
    """`extractZipToDirectory`: every entry written under `target_dir`, executable bits restored."""
    with open(zip_path, "rb") as handle:
        zip_buf = handle.read()
    files = await unzip_file(zip_buf)
    modes = parse_zip_modes(zip_buf)
    os.makedirs(target_dir, exist_ok=True)
    for rel_path, data in files.items():
        if rel_path.endswith("/"):
            os.makedirs(os.path.join(target_dir, rel_path), exist_ok=True)
            continue
        full_path = os.path.join(target_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as out:
            out.write(data)
        mode = modes.get(rel_path)
        if mode and mode & 0o111:
            try:
                os.chmod(full_path, mode & 0o777)
            except OSError:
                pass
    logger.debug("Extracted ZIP to %s: %d entries", target_dir, len(files))


async def convert_directory_to_zip_in_place(dir_path: str, zip_path: str) -> None:
    """`convertDirectoryToZipInPlace`: zip, write atomically, remove the directory."""
    zip_data = await create_zip_from_directory(dir_path)
    await atomic_write_to_zip_cache(zip_path, zip_data)
    shutil.rmtree(dir_path, ignore_errors=True)


def get_marketplace_json_relative_path(marketplace_name: str) -> str:
    """`getMarketplaceJsonRelativePath`: `marketplaces/<sanitized name>.json`."""
    sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "-", marketplace_name)
    return os.path.join("marketplaces", f"{sanitized}.json")


def is_marketplace_source_supported_by_zip_cache(source: Mapping[str, Any]) -> bool:
    """`isMarketplaceSourceSupportedByZipCache`: github, git, url and settings sources."""
    return source.get("source") in ("github", "git", "url", "settings")


__all__ = [
    "PLUGIN_USE_ZIP_CACHE_ENV",
    "SESSION_PLUGIN_CACHE_PREFIX",
    "ZIP_COMPRESSION_LEVEL",
    "atomic_write_to_zip_cache",
    "cleanup_session_plugin_cache",
    "convert_directory_to_zip_in_place",
    "create_zip_from_directory",
    "extract_zip_to_directory",
    "get_marketplace_json_relative_path",
    "get_plugin_zip_cache_path",
    "get_session_plugin_cache_path",
    "get_zip_cache_installed_plugins_path",
    "get_zip_cache_known_marketplaces_path",
    "get_zip_cache_marketplaces_dir",
    "get_zip_cache_plugins_dir",
    "is_marketplace_source_supported_by_zip_cache",
    "is_plugin_zip_cache_enabled",
    "reset_session_plugin_cache",
]
