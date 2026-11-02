"""
MCPB/DXT bundles a plugin ships as MCP servers: download or read, extract into a content-hash
cache, validate the manifest, and the per-server user configuration.

Ported from `reference/claude-code/src/utils/plugins/mcpbHandler.ts` -> `UserConfigValues`,
`UserConfigSchema`, `McpbLoadResult`, `McpbNeedsConfigResult`, `McpbCacheMetadata`,
`ProgressCallback`, `isMcpbSource`, `isUrl`, `generateContentHash`, `getMcpbCacheDir`,
`getMetadataPath`, `serverSecretsKey`, `loadMcpServerUserConfig`, `saveMcpServerUserConfig`,
`validateUserConfig`, `generateMcpConfig`, `loadCacheMetadata`, `saveCacheMetadata`,
`downloadMcpb`, `extractMcpbContents`, `checkMcpbChanged`, `loadMcpbFile`.

A bundle is extracted to `<plugin>/.mcpb-cache/<content hash>/`, with
`<plugin>/.mcpb-cache/<md5(source)[:8]>.metadata.json` recording it; a local bundle whose mtime
is newer than the record is extracted again (a URL is re-fetched only on an explicit update).
Per-server user configuration is split by the schema's `sensitive`: sensitive values in secure
storage (`pluginSecrets["<pluginId>/<server>"]`), the rest in user settings
(`pluginConfigs[pluginId].mcpServers[server]`); a read merges both, secure storage winning.

`McpbUserConfigurationOption` (from `@anthropic-ai/mcpb`) has the same shape as the manifest's
`PluginUserConfigOption`, so that schema stands in for it, as upstream's comment intends. The
manifest schema and `getMcpConfigForManifest` are the package's (`app/utils/dxt/mcpb.py`).

`locations` names the run's settings sources (upstream reads and writes its process-wide
settings). `mcpConfig` is the raw server entry (`command`, `args`, `env`, ...), a plain dict as
upstream's object is; scoping it is the MCP integration's job.

Divergences: the fetch telemetry (`logPluginFetch`, `classifyFetchError`) is not ported, as in the
rest of the plugin port. As `plugin_options_storage.save_plugin_options` does, the settings write
is only the `pluginConfigs[pluginId].mcpServers[server]` patch, not upstream's whole merged
settings object (which can copy other sources' keys into the user file). The download uses
`httpx` (axios's 2-minute timeout and 5 redirects; a non-2xx status raises axios's
`Request failed with status code <n>`), streamed so the percentage progress still fires.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypeAlias, TypedDict

import httpx

from app.types.permissions import PermissionRuleSource
from app.utils.dxt.helpers import parse_and_validate_manifest_from_bytes
from app.utils.dxt.mcpb import McpbManifest, get_mcp_config_for_manifest
from app.utils.dxt.zip import parse_zip_modes, unzip_file
from app.utils.permissions.permission_update import update_settings_for_source
from app.utils.plugins.schemas import PluginUserConfigOption
from app.utils.secure_storage.index import get_secure_storage
from app.utils.settings.settings import SettingsLocations, get_initial_settings
from app.utils.system_directories import get_system_directories

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `UserConfigValues`.
UserConfigValues: TypeAlias = dict[str, str | float | bool | list[str]]
#: `UserConfigSchema`.
UserConfigSchema: TypeAlias = dict[str, PluginUserConfigOption]
#: `ProgressCallback`.
ProgressCallback: TypeAlias = Callable[[str], None]
#: The download seam: `(url, timeout_ms, on_bytes(loaded, total))` -> the body.
HttpGetBytes: TypeAlias = Callable[[str, int, Callable[[int, int | None], None]], Awaitable[bytes]]

#: `axios.get(url, {timeout: 120000})`: a 2-minute download timeout.
MCPB_DOWNLOAD_TIMEOUT_MS = 120000
#: `maxRedirects: 5` (follow redirects like `curl -L`).
MCPB_DOWNLOAD_MAX_REDIRECTS = 5
MCPB_CACHE_DIRNAME = ".mcpb-cache"
_TEXT_EXTENSIONS = (".json", ".js", ".ts", ".txt", ".md", ".yml", ".yaml")


@dataclass(frozen=True)
class McpbLoadResult:
    """`McpbLoadResult`: the manifest, the server's `mcpConfig`, where it was extracted, its hash."""

    manifest: McpbManifest
    mcp_config: dict[str, Any]
    extracted_path: str
    content_hash: str


@dataclass(frozen=True)
class McpbNeedsConfigResult:
    """`McpbNeedsConfigResult`: the bundle needs (re)configuration before it can run."""

    manifest: McpbManifest
    extracted_path: str
    content_hash: str
    config_schema: UserConfigSchema
    existing_config: UserConfigValues
    validation_errors: list[str]
    status: Literal["needs-config"] = "needs-config"


class McpbCacheMetadata(TypedDict):
    """`McpbCacheMetadata`, as stored (upstream's keys)."""

    source: str
    contentHash: str
    extractedPath: str
    cachedAt: str
    lastChecked: str


@dataclass(frozen=True)
class UserConfigValidation:
    """`validateUserConfig`'s `{valid, errors}`."""

    valid: bool
    errors: list[str] = field(default_factory=list)


def is_mcpb_source(source: str) -> bool:
    """`isMcpbSource`: a `.mcpb` or `.dxt` reference."""
    return source.endswith((".mcpb", ".dxt"))


def _is_url(source: str) -> bool:
    """`isUrl`."""
    return source.startswith(("http://", "https://"))


def _generate_content_hash(data: bytes) -> str:
    """`generateContentHash`: the first 16 hex digits of the SHA-256."""
    return hashlib.sha256(data).hexdigest()[:16]


def _get_mcpb_cache_dir(plugin_path: str) -> str:
    """`getMcpbCacheDir`."""
    return os.path.join(plugin_path, MCPB_CACHE_DIRNAME)


def _source_hash(source: str) -> str:
    return hashlib.md5(source.encode("utf-8")).hexdigest()[:8]


def _get_metadata_path(cache_dir: str, source: str) -> str:
    """`getMetadataPath`: `<cache dir>/<md5(source)[:8]>.metadata.json`."""
    return os.path.join(cache_dir, f"{_source_hash(source)}.metadata.json")


def _server_secrets_key(plugin_id: str, server_name: str) -> str:
    """`serverSecretsKey`: `<pluginId>/<server>`, a key in the flat `pluginSecrets` map."""
    return f"{plugin_id}/{server_name}"


def _settings_server_config(settings: Mapping[str, Any], plugin_id: str, server_name: str) -> dict[str, Any] | None:
    configs = settings.get("pluginConfigs")
    entry = configs.get(plugin_id) if isinstance(configs, dict) else None
    servers = entry.get("mcpServers") if isinstance(entry, dict) else None
    server = servers.get(server_name) if isinstance(servers, dict) else None
    return dict(server) if isinstance(server, dict) else None


def _stored_server_secrets(data: Mapping[str, Any] | None, key: str) -> dict[str, Any] | None:
    secrets = (data or {}).get("pluginSecrets")
    entry = secrets.get(key) if isinstance(secrets, dict) else None
    return dict(entry) if isinstance(entry, dict) else None


def load_mcp_server_user_config(
    plugin_id: str, server_name: str, locations: SettingsLocations
) -> UserConfigValues | None:
    """
    `loadMcpServerUserConfig`: the settings values merged with the secure-storage ones (secure
    storage wins); `None` when neither store has an entry, or the read fails.
    """
    try:
        non_sensitive = _settings_server_config(get_initial_settings(locations), plugin_id, server_name)
        sensitive = _stored_server_secrets(get_secure_storage().read(), _server_secrets_key(plugin_id, server_name))
        if non_sensitive is None and sensitive is None:
            return None
        logger.debug("Loaded user config for %s/%s (settings + secureStorage)", plugin_id, server_name)
        return {**(non_sensitive or {}), **(sensitive or {})}
    except Exception as exc:  # noqa: BLE001 - upstream logs and answers null on any failure
        logger.error("Failed to load user config for %s/%s: %s", plugin_id, server_name, exc)
        return None


def _js_string(value: Any) -> str:
    """JavaScript's `String(value)` for a config value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ",".join(_js_string(v) for v in value)
    return str(value)


def save_mcp_server_user_config(
    plugin_id: str,
    server_name: str,
    config: Mapping[str, Any],
    schema: Mapping[str, Mapping[str, Any]],
    locations: SettingsLocations,
) -> None:
    """
    `saveMcpServerUserConfig`: sensitive values to secure storage first (a failure raises before
    settings are touched), then the rest to user settings. Keys written to one store in this
    call are scrubbed from the other; keys not in `config` are left alone in both.
    """
    try:
        non_sensitive: dict[str, Any] = {}
        sensitive: dict[str, str] = {}
        for key, value in config.items():
            if (schema.get(key) or {}).get("sensitive") is True:
                sensitive[key] = _js_string(value)
            else:
                non_sensitive[key] = value

        storage = get_secure_storage()
        secret_key = _server_secrets_key(plugin_id, server_name)
        existing_secret = _stored_server_secrets(storage.read(), secret_key)
        secure_scrubbed = (
            {k: v for k, v in existing_secret.items() if k not in non_sensitive} if existing_secret else None
        )
        need_secure_scrub = (
            secure_scrubbed is not None
            and existing_secret is not None
            and len(secure_scrubbed) != len(existing_secret)
        )
        if sensitive or need_secure_scrub:
            existing = storage.read() or {}
            plugin_secrets = existing.get("pluginSecrets")
            if not isinstance(plugin_secrets, dict):
                plugin_secrets = {}
                existing["pluginSecrets"] = plugin_secrets
            plugin_secrets[secret_key] = {**(secure_scrubbed or {}), **sensitive}
            result = storage.update(existing)
            if not result.success:
                raise RuntimeError(f"Failed to save sensitive config to secure storage for {secret_key}")
            if result.warning:
                logger.warning("Server secrets save warning: %s", result.warning)
            if need_secure_scrub and existing_secret is not None and secure_scrubbed is not None:
                logger.debug(
                    "saveMcpServerUserConfig: scrubbed %d stale non-sensitive key(s) from secureStorage for %s",
                    len(existing_secret) - len(secure_scrubbed),
                    secret_key,
                )

        existing_in_settings = (
            _settings_server_config(get_initial_settings(locations), plugin_id, server_name) or {}
        )
        keys_to_scrub = [k for k in existing_in_settings if k in sensitive]
        if non_sensitive or keys_to_scrub:
            server_entry: dict[str, Any] = {**non_sensitive, **{k: None for k in keys_to_scrub}}
            error = update_settings_for_source(
                PermissionRuleSource.USER_SETTINGS,
                locations,
                {"pluginConfigs": {plugin_id: {"mcpServers": {server_name: server_entry}}}},
            )
            if error is not None:
                raise RuntimeError(error)
            if keys_to_scrub:
                logger.debug(
                    "saveMcpServerUserConfig: scrubbed %d plaintext sensitive key(s) from settings.json for %s/%s",
                    len(keys_to_scrub),
                    plugin_id,
                    server_name,
                )
        logger.debug(
            "Saved user config for %s/%s (%d non-sensitive, %d sensitive)",
            plugin_id,
            server_name,
            len(non_sensitive),
            len(sensitive),
        )
    except Exception as exc:
        logger.error("Failed to save user configuration for %s/%s: %s", plugin_id, server_name, exc)
        raise RuntimeError(f"Failed to save user configuration for {plugin_id}/{server_name}: {exc}") from exc


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _js_number(value: float) -> str:
    """`${n}` for a JS number: integral floats print without `.0`."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


_ABSENT = object()


def validate_user_config(
    values: Mapping[str, Any], schema: Mapping[str, Mapping[str, Any]]
) -> UserConfigValidation:
    """`validateUserConfig`: every field of the schema checked against the values."""
    errors: list[str] = []
    for key, field_schema in schema.items():
        # `undefined` is an absent key; a stored `null` is a value and fails the type check.
        value = values.get(key, _ABSENT)
        absent = value is _ABSENT or (isinstance(value, str) and value == "")
        label = field_schema.get("title") or key
        if field_schema.get("required") and absent:
            errors.append(f"{label} is required but not provided")
            continue
        if absent:
            continue
        kind = field_schema.get("type")
        if kind == "string":
            if isinstance(value, list):
                if not field_schema.get("multiple"):
                    errors.append(f"{label} must be a string, not an array")
                elif not all(isinstance(v, str) for v in value):
                    errors.append(f"{label} must be an array of strings")
            elif not isinstance(value, str):
                errors.append(f"{label} must be a string")
        elif kind == "number" and not _is_number(value):
            errors.append(f"{label} must be a number")
        elif kind == "boolean" and not isinstance(value, bool):
            errors.append(f"{label} must be a boolean")
        elif kind in ("file", "directory") and not isinstance(value, str):
            errors.append(f"{label} must be a path string")
        if kind == "number" and _is_number(value):
            minimum = field_schema.get("min")
            maximum = field_schema.get("max")
            if minimum is not None and value < minimum:
                errors.append(f"{label} must be at least {_js_number(minimum)}")
            if maximum is not None and value > maximum:
                errors.append(f"{label} must be at most {_js_number(maximum)}")
    return UserConfigValidation(valid=not errors, errors=errors)


async def _generate_mcp_config(
    manifest: McpbManifest, extracted_path: str, user_config: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """`generateMcpConfig`: the package's `getMcpConfigForManifest`, `/` as the separator; `None` raises."""
    mcp_config = await get_mcp_config_for_manifest(
        manifest, extracted_path, get_system_directories(), user_config if user_config is not None else {}, "/"
    )
    if not mcp_config:
        message = f'Failed to generate MCP server configuration from manifest "{manifest.get("name")}"'
        logger.error(message)
        raise RuntimeError(message)
    return mcp_config


async def _load_cache_metadata(cache_dir: str, source: str) -> McpbCacheMetadata | None:
    """`loadCacheMetadata`: `None` when absent; any other failure is logged and is `None` too."""
    metadata_path = _get_metadata_path(cache_dir, source)
    try:
        with open(metadata_path, encoding="utf-8") as handle:
            data: McpbCacheMetadata = json.loads(handle.read())
        return data
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - upstream logs a corrupt or unreadable record and re-extracts
        logger.error("Failed to load MCPB cache metadata: %s", exc)
        return None


async def _save_cache_metadata(cache_dir: str, source: str, metadata: McpbCacheMetadata) -> None:
    """`saveCacheMetadata`."""
    os.makedirs(cache_dir, exist_ok=True)
    with open(_get_metadata_path(cache_dir, source), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(metadata, indent=2))


async def http_get_bytes(url: str, timeout_ms: int, on_bytes: Callable[[int, int | None], None]) -> bytes:
    """
    `axios.get(url, {responseType: 'arraybuffer', maxRedirects: 5, onDownloadProgress})`: the
    body, `on_bytes(loaded, total)` after each chunk. The seam tests replace.
    """
    async with httpx.AsyncClient(
        timeout=timeout_ms / 1000, follow_redirects=True, max_redirects=MCPB_DOWNLOAD_MAX_REDIRECTS
    ) as client, client.stream("GET", url) as response:
        if not 200 <= response.status_code < 300:
            raise httpx.HTTPStatusError(
                f"Request failed with status code {response.status_code}",
                request=response.request,
                response=response,
            )
        header = response.headers.get("content-length")
        total = int(header) if header and header.isdigit() else None
        chunks: list[bytes] = []
        loaded = 0
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            loaded += len(chunk)
            on_bytes(loaded, total)
    return b"".join(chunks)


def _js_round(value: float) -> int:
    """`Math.round`: halves round up."""
    return math.floor(value + 0.5)


async def _download_mcpb(url: str, dest_path: str, on_progress: ProgressCallback | None = None) -> bytes:
    """`downloadMcpb`: fetch the bundle, write it to `dest_path`; any failure is wrapped."""
    logger.debug("Downloading MCPB from %s", url)
    if on_progress is not None:
        on_progress(f"Downloading {url}...")

    def report(loaded: int, total: int | None) -> None:
        if total and on_progress is not None:
            on_progress(f"Downloading... {_js_round(loaded / total * 100)}%")

    try:
        data = await http_get_bytes(url, MCPB_DOWNLOAD_TIMEOUT_MS, report)
        with open(dest_path, "wb") as handle:
            handle.write(data)
        logger.debug("Downloaded %d bytes to %s", len(data), dest_path)
        if on_progress is not None:
            on_progress("Download complete")
        return data
    except Exception as exc:
        message = f"Failed to download MCPB file from {url}: {exc}"
        logger.error(message)
        raise RuntimeError(message) from exc


async def _extract_mcpb_contents(
    unzipped: Mapping[str, bytes],
    extract_path: str,
    modes: Mapping[str, int],
    on_progress: ProgressCallback | None = None,
) -> None:
    """
    `extractMcpbContents`: write every file entry (directory entries skipped); text files are
    decoded as UTF-8 and written back (`TextDecoder`), the rest byte for byte; an executable
    mode is restored (a chmod failure is swallowed).
    """
    if on_progress is not None:
        on_progress("Extracting files...")
    os.makedirs(extract_path, exist_ok=True)
    entries = [(k, v) for k, v in unzipped.items() if not k.endswith("/")]
    total_files = len(entries)
    files_written = 0
    for file_path, file_data in entries:
        full_path = os.path.join(extract_path, file_path)
        directory = os.path.dirname(full_path)
        if directory != extract_path:
            os.makedirs(directory, exist_ok=True)
        if file_path.endswith(_TEXT_EXTENSIONS):
            content = file_data.decode("utf-8", errors="replace").removeprefix("﻿")
            with open(full_path, "w", encoding="utf-8") as text_handle:
                text_handle.write(content)
        else:
            with open(full_path, "wb") as binary_handle:
                binary_handle.write(file_data)
        mode = modes.get(file_path)
        if mode and mode & 0o111:
            try:
                os.chmod(full_path, mode & 0o777)
            except OSError:
                pass
        files_written += 1
        if on_progress is not None and files_written % 10 == 0:
            on_progress(f"Extracted {files_written}/{total_files} files")
    logger.debug("Extracted %d files to %s", files_written, extract_path)
    if on_progress is not None:
        on_progress(f"Extraction complete ({files_written} files)")


def _iso_now() -> str:
    """`new Date().toISOString()`."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso_to_ms(value: str) -> float:
    """`new Date(value).getTime()`: `NaN` when it does not parse."""
    try:
        return datetime.fromisoformat(value).timestamp() * 1000
    except (TypeError, ValueError):
        return math.nan


async def check_mcpb_changed(source: str, plugin_path: str) -> bool:
    """
    `checkMcpbChanged`: `True` without a record, when the extraction is gone, or when a local
    bundle is missing or modified after the record was written. A URL counts as unchanged.
    """
    metadata = await _load_cache_metadata(_get_mcpb_cache_dir(plugin_path), source)
    if metadata is None:
        return True
    extracted_path: Any = metadata.get("extractedPath")
    try:
        os.stat(extracted_path)
    except FileNotFoundError:
        logger.debug("MCPB extraction path missing: %s", extracted_path)
        return True
    except (OSError, TypeError, ValueError) as exc:
        # A record without a usable path fails the stat as upstream's `stat(undefined)` does.
        logger.error("MCPB extraction path inaccessible: %s: %s", extracted_path, exc)
        return True
    if not _is_url(source):
        local_path = os.path.join(plugin_path, source)
        try:
            stats = os.stat(local_path)
        except FileNotFoundError:
            logger.debug("MCPB source file missing: %s", local_path)
            return True
        except OSError as exc:
            logger.error("MCPB source file inaccessible: %s: %s", local_path, exc)
            return True
        cached_time = _iso_to_ms(str(metadata.get("cachedAt")))
        # Floored to the millisecond precision of `cachedAt`.
        file_time = math.floor(stats.st_mtime_ns / 1_000_000)
        if file_time > cached_time:
            logger.debug("MCPB file modified: %s > %s", file_time, cached_time)
            return True
    return False


def _new_metadata(source: str, content_hash: str, extracted_path: str) -> McpbCacheMetadata:
    now = _iso_now()
    return {
        "source": source,
        "contentHash": content_hash,
        "extractedPath": extracted_path,
        "cachedAt": now,
        "lastChecked": now,
    }


async def load_mcpb_file(
    source: str,
    plugin_path: str,
    plugin_id: str,
    locations: SettingsLocations,
    on_progress: ProgressCallback | None = None,
    provided_user_config: Mapping[str, Any] | None = None,
    force_config_dialog: bool = False,
) -> McpbLoadResult | McpbNeedsConfigResult:
    """
    `loadMcpbFile`: the cached extraction when it is current, else the bundle downloaded (a URL)
    or read (relative to the plugin), extracted under its content hash, its manifest validated.
    A manifest with `user_config` needs the saved or provided values to validate; otherwise
    (or when `force_config_dialog`, on the cached path) the result is `needs-config`. Provided
    values are saved once they validate.
    """
    cache_dir = _get_mcpb_cache_dir(plugin_path)
    os.makedirs(cache_dir, exist_ok=True)
    logger.debug("Loading MCPB from source: %s", source)

    metadata = await _load_cache_metadata(cache_dir, source)
    if metadata is not None and not await check_mcpb_changed(source, plugin_path):
        logger.debug("Using cached MCPB from %s (hash: %s)", metadata["extractedPath"], metadata["contentHash"])
        manifest_path = os.path.join(metadata["extractedPath"], "manifest.json")
        try:
            with open(manifest_path, encoding="utf-8") as handle:
                manifest_content = handle.read()
        except FileNotFoundError as exc:
            message = f"Cached manifest not found: {manifest_path}"
            logger.error(message)
            raise RuntimeError(message) from exc
        manifest = await parse_and_validate_manifest_from_bytes(manifest_content.encode("utf-8"))
        user_config_schema = manifest.get("user_config")
        if user_config_schema:
            server_name = manifest["name"]
            saved_config = load_mcp_server_user_config(plugin_id, server_name, locations)
            user_config = provided_user_config or saved_config or {}
            validation = validate_user_config(user_config, user_config_schema)
            if force_config_dialog or not validation.valid:
                return McpbNeedsConfigResult(
                    manifest=manifest,
                    extracted_path=metadata["extractedPath"],
                    content_hash=metadata["contentHash"],
                    config_schema=user_config_schema,
                    existing_config=saved_config or {},
                    validation_errors=[] if validation.valid else validation.errors,
                )
            if provided_user_config:
                save_mcp_server_user_config(plugin_id, server_name, provided_user_config, user_config_schema, locations)
            mcp_config = await _generate_mcp_config(manifest, metadata["extractedPath"], user_config)
            return McpbLoadResult(manifest, mcp_config, metadata["extractedPath"], metadata["contentHash"])
        mcp_config = await _generate_mcp_config(manifest, metadata["extractedPath"])
        return McpbLoadResult(manifest, mcp_config, metadata["extractedPath"], metadata["contentHash"])

    if _is_url(source):
        mcpb_file_path = os.path.join(cache_dir, f"{_source_hash(source)}.mcpb")
        mcpb_data = await _download_mcpb(source, mcpb_file_path, on_progress)
    else:
        local_path = os.path.join(plugin_path, source)
        if on_progress is not None:
            on_progress(f"Loading {source}...")
        try:
            with open(local_path, "rb") as bundle:
                mcpb_data = bundle.read()
        except FileNotFoundError as exc:
            message = f"MCPB file not found: {local_path}"
            logger.error(message)
            raise RuntimeError(message) from exc

    content_hash = _generate_content_hash(mcpb_data)
    logger.debug("MCPB content hash: %s", content_hash)
    if on_progress is not None:
        on_progress("Extracting MCPB archive...")
    unzipped = await unzip_file(mcpb_data)
    modes = parse_zip_modes(mcpb_data)
    if "manifest.json" not in unzipped:
        message = "No manifest.json found in MCPB file"
        logger.error(message)
        raise RuntimeError(message)
    manifest = await parse_and_validate_manifest_from_bytes(unzipped["manifest.json"])
    logger.debug(
        "MCPB manifest: %s v%s by %s", manifest.get("name"), manifest.get("version"), manifest["author"]["name"]
    )
    if not manifest.get("server"):
        message = f'MCPB manifest for "{manifest.get("name")}" does not define a server configuration'
        logger.error(message)
        raise RuntimeError(message)

    extract_path = os.path.join(cache_dir, content_hash)
    await _extract_mcpb_contents(unzipped, extract_path, modes, on_progress)

    user_config_schema = manifest.get("user_config")
    if user_config_schema:
        server_name = manifest["name"]
        saved_config = load_mcp_server_user_config(plugin_id, server_name, locations)
        user_config = provided_user_config or saved_config or {}
        validation = validate_user_config(user_config, user_config_schema)
        if not validation.valid:
            await _save_cache_metadata(cache_dir, source, _new_metadata(source, content_hash, extract_path))
            return McpbNeedsConfigResult(
                manifest=manifest,
                extracted_path=extract_path,
                content_hash=content_hash,
                config_schema=user_config_schema,
                existing_config=saved_config or {},
                validation_errors=validation.errors,
            )
        if provided_user_config:
            save_mcp_server_user_config(plugin_id, server_name, provided_user_config, user_config_schema, locations)
        if on_progress is not None:
            on_progress("Generating MCP server configuration...")
        mcp_config = await _generate_mcp_config(manifest, extract_path, user_config)
        await _save_cache_metadata(cache_dir, source, _new_metadata(source, content_hash, extract_path))
        return McpbLoadResult(manifest, mcp_config, extract_path, content_hash)

    if on_progress is not None:
        on_progress("Generating MCP server configuration...")
    mcp_config = await _generate_mcp_config(manifest, extract_path)
    await _save_cache_metadata(cache_dir, source, _new_metadata(source, content_hash, extract_path))
    logger.debug("Successfully loaded MCPB: %s (extracted to %s)", manifest.get("name"), extract_path)
    return McpbLoadResult(manifest, mcp_config, extract_path, content_hash)


__all__ = [
    "MCPB_CACHE_DIRNAME",
    "MCPB_DOWNLOAD_MAX_REDIRECTS",
    "MCPB_DOWNLOAD_TIMEOUT_MS",
    "HttpGetBytes",
    "McpbCacheMetadata",
    "McpbLoadResult",
    "McpbNeedsConfigResult",
    "ProgressCallback",
    "UserConfigSchema",
    "UserConfigValidation",
    "UserConfigValues",
    "check_mcpb_changed",
    "http_get_bytes",
    "is_mcpb_source",
    "load_mcp_server_user_config",
    "load_mcpb_file",
    "save_mcp_server_user_config",
    "validate_user_config",
]
