"""
Flagged plugins: plugins auto-removed because their marketplace delisted them.

Ported from `reference/claude-code/src/utils/plugins/pluginFlagging.ts` -> `FLAGGED_PLUGINS_FILENAME`,
`FlaggedPlugin`, `SEEN_EXPIRY_MS`, `parsePluginsData`, `readFromDisk`, `writeToDisk`,
`loadFlaggedPlugins`, `getFlaggedPlugins`, `addFlaggedPlugin`, `markFlaggedPluginsSeen`,
`removeFlaggedPlugin`.

Stored in `<plugins dir>/flagged-plugins.json`, mode 0600, written atomically (temp file and
rename). A module-level cache serves `get_flagged_plugins` synchronously; it is filled by the
first async call. An entry seen more than 48 hours ago is dropped on the next load.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from app.utils.plugins.plugin_directories import get_plugins_directory

logger = logging.getLogger("TDDOrchestrator.Plugins")

FLAGGED_PLUGINS_FILENAME = "flagged-plugins.json"
#: `SEEN_EXPIRY_MS`: 48 hours.
SEEN_EXPIRY_MS = 48 * 60 * 60 * 1000


class FlaggedPlugin(TypedDict):
    """`FlaggedPlugin`."""

    flaggedAt: str
    seenAt: NotRequired[str]


_cache: dict[str, FlaggedPlugin] | None = None


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso_ms(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp() * 1000
    except ValueError:
        return None


def get_flagged_plugins_path() -> str:
    """`getFlaggedPluginsPath`."""
    return os.path.join(get_plugins_directory(), FLAGGED_PLUGINS_FILENAME)


def parse_plugins_data(content: str) -> dict[str, FlaggedPlugin]:
    """`parsePluginsData`: well-formed entries only; anything else is `{}`."""
    parsed: Any = json.loads(content)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("plugins"), dict):
        return {}
    result: dict[str, FlaggedPlugin] = {}
    for plugin_id, entry in parsed["plugins"].items():
        if isinstance(entry, dict) and isinstance(entry.get("flaggedAt"), str):
            item: FlaggedPlugin = {"flaggedAt": entry["flaggedAt"]}
            if isinstance(entry.get("seenAt"), str):
                item["seenAt"] = entry["seenAt"]
            result[plugin_id] = item
    return result


async def _read_from_disk() -> dict[str, FlaggedPlugin]:
    try:
        return parse_plugins_data(Path(get_flagged_plugins_path()).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}


async def _write_to_disk(plugins: dict[str, FlaggedPlugin]) -> None:
    global _cache
    path = get_flagged_plugins_path()
    temp = f"{path}.{secrets.token_hex(8)}.tmp"
    try:
        os.makedirs(get_plugins_directory(), exist_ok=True)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"plugins": plugins}, indent=2))
        os.replace(temp, path)
        _cache = plugins
    except OSError as exc:
        logger.error("Failed to write flagged plugins: %s", exc)
        try:
            os.unlink(temp)
        except OSError:
            pass


async def load_flagged_plugins() -> None:
    """`loadFlaggedPlugins`: fill the cache, dropping entries seen 48 hours ago or more."""
    global _cache
    all_plugins = await _read_from_disk()
    now = datetime.now(UTC).timestamp() * 1000
    changed = False
    for plugin_id, entry in list(all_plugins.items()):
        seen = entry.get("seenAt")
        seen_ms = _parse_iso_ms(seen) if seen else None
        if seen_ms is not None and now - seen_ms >= SEEN_EXPIRY_MS:
            del all_plugins[plugin_id]
            changed = True
    _cache = all_plugins
    if changed:
        await _write_to_disk(all_plugins)


def get_flagged_plugins() -> dict[str, FlaggedPlugin]:
    """`getFlaggedPlugins`: the cache, `{}` before the first load."""
    return _cache if _cache is not None else {}


async def add_flagged_plugin(plugin_id: str) -> None:
    """`addFlaggedPlugin`."""
    global _cache
    if _cache is None:
        _cache = await _read_from_disk()
    updated: dict[str, FlaggedPlugin] = {**_cache, plugin_id: {"flaggedAt": _iso_now()}}
    await _write_to_disk(updated)
    logger.debug("Flagged plugin: %s", plugin_id)


async def mark_flagged_plugins_seen(plugin_ids: list[str]) -> None:
    """`markFlaggedPluginsSeen`: set `seenAt` on entries that lack it."""
    global _cache
    if _cache is None:
        _cache = await _read_from_disk()
    now = _iso_now()
    updated = dict(_cache)
    changed = False
    for plugin_id in plugin_ids:
        entry = updated.get(plugin_id)
        if entry is not None and not entry.get("seenAt"):
            updated[plugin_id] = {**entry, "seenAt": now}
            changed = True
    if changed:
        await _write_to_disk(updated)


async def remove_flagged_plugin(plugin_id: str) -> None:
    """`removeFlaggedPlugin`."""
    global _cache
    if _cache is None:
        _cache = await _read_from_disk()
    if plugin_id not in _cache:
        return
    rest = {k: v for k, v in _cache.items() if k != plugin_id}
    _cache = rest
    await _write_to_disk(rest)


def reset_flagged_plugins_cache() -> None:
    """Forget the cache (tests; upstream resets module state between tests)."""
    global _cache
    _cache = None


__all__ = [
    "FLAGGED_PLUGINS_FILENAME",
    "SEEN_EXPIRY_MS",
    "FlaggedPlugin",
    "add_flagged_plugin",
    "get_flagged_plugins",
    "get_flagged_plugins_path",
    "load_flagged_plugins",
    "mark_flagged_plugins_seen",
    "parse_plugins_data",
    "remove_flagged_plugin",
    "reset_flagged_plugins_cache",
]
