"""
The zip cache's metadata I/O: its `known_marketplaces.json` and the marketplace JSON copies that
let an ephemeral container load marketplaces without cloning them again.

Ported from `reference/claude-code/src/utils/plugins/zipCacheAdapters.ts` ->
`readZipCacheKnownMarketplaces`, `writeZipCacheKnownMarketplaces`, `readMarketplaceJson`,
`saveMarketplaceJsonToZipCache`, `readMarketplaceJsonContent`, `syncMarketplacesToZipCache`.

A marketplace directory holds its JSON at `<PLUGIN_PACKAGE_DIR>/marketplace.json` (upstream's
`.claude-plugin/`, renamed by user decision; see `plugin_directories.PLUGIN_PACKAGE_DIR`).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from typing import Any, cast

from app.utils.plugins.marketplace_manager import load_known_marketplaces_config_safe
from app.utils.plugins.plugin_directories import PLUGIN_PACKAGE_DIR
from app.utils.plugins.schemas import (
    KnownMarketplacesFile,
    KnownMarketplacesFileSchema,
    PluginMarketplace,
    PluginMarketplaceSchema,
    format_issues,
    safe_parse,
)
from app.utils.plugins.zip_cache import (
    atomic_write_to_zip_cache,
    get_marketplace_json_relative_path,
    get_plugin_zip_cache_path,
    get_zip_cache_known_marketplaces_path,
)

logger = logging.getLogger("TDDOrchestrator.Plugins")


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


async def read_zip_cache_known_marketplaces() -> KnownMarketplacesFile:
    """`readZipCacheKnownMarketplaces`: `{}` when missing, unparsable or invalid (another container may write it)."""
    try:
        content = _read_text(get_zip_cache_known_marketplaces_path())
        parsed = safe_parse(KnownMarketplacesFileSchema, json.loads(content))
    except Exception:  # noqa: BLE001 - upstream's bare catch: any read or parse failure is an empty cache
        return {}
    if not parsed.success or parsed.data is None:
        logger.error("Invalid known_marketplaces.json in zip cache: %s", format_issues(parsed.issues))
        return {}
    return parsed.data


async def write_zip_cache_known_marketplaces(data: Mapping[str, Any]) -> None:
    """`writeZipCacheKnownMarketplaces`: written atomically, two-space indented."""
    await atomic_write_to_zip_cache(get_zip_cache_known_marketplaces_path(), json.dumps(data, indent=2))


async def read_marketplace_json(marketplace_name: str) -> PluginMarketplace | None:
    """`readMarketplaceJson`: the cached marketplace JSON, `None` when absent or invalid."""
    zip_cache_path = get_plugin_zip_cache_path()
    if not zip_cache_path:
        return None
    full_path = os.path.join(zip_cache_path, get_marketplace_json_relative_path(marketplace_name))
    try:
        parsed: Any = json.loads(_read_text(full_path))
    except (OSError, ValueError):
        return None
    result = safe_parse(PluginMarketplaceSchema, parsed)
    if result.success:
        return cast(PluginMarketplace, result.data)
    logger.debug("Invalid marketplace JSON for %s: %s", marketplace_name, format_issues(result.issues))
    return None


async def save_marketplace_json_to_zip_cache(marketplace_name: str, install_location: str) -> None:
    """`saveMarketplaceJsonToZipCache`: copy the marketplace JSON from its install location."""
    zip_cache_path = get_plugin_zip_cache_path()
    if not zip_cache_path:
        return
    content = await _read_marketplace_json_content(install_location)
    if content is not None:
        rel_path = get_marketplace_json_relative_path(marketplace_name)
        await atomic_write_to_zip_cache(os.path.join(zip_cache_path, rel_path), content)


async def _read_marketplace_json_content(directory: str) -> str | None:
    """`readMarketplaceJsonContent`: the package folder's JSON, a root `marketplace.json`, or the location itself."""
    candidates = [
        os.path.join(directory, PLUGIN_PACKAGE_DIR, "marketplace.json"),
        os.path.join(directory, "marketplace.json"),
        directory,
    ]
    for candidate in candidates:
        try:
            return _read_text(candidate)
        except (OSError, UnicodeDecodeError):
            continue
    return None


async def sync_marketplaces_to_zip_cache() -> None:
    """`syncMarketplacesToZipCache`: save every known marketplace's JSON, then merge the known-marketplaces file."""
    known_marketplaces = await load_known_marketplaces_config_safe()
    for name, entry in known_marketplaces.items():
        install_location = entry.get("installLocation")
        if not install_location:
            continue
        try:
            await save_marketplace_json_to_zip_cache(name, install_location)
        except Exception as exc:  # noqa: BLE001 - one marketplace's failure must not stop the others
            logger.debug("Failed to save marketplace JSON for %s: %s", name, exc)
    cached = await read_zip_cache_known_marketplaces()
    await write_zip_cache_known_marketplaces({**cached, **known_marketplaces})


__all__ = [
    "read_marketplace_json",
    "read_zip_cache_known_marketplaces",
    "save_marketplace_json_to_zip_cache",
    "sync_marketplaces_to_zip_cache",
    "write_zip_cache_known_marketplaces",
]
