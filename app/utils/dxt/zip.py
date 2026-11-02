"""
Zip extraction for MCPB/DXT bundles and the plugin zip cache: safe-path and zip-bomb limits,
and the Unix modes of a zip's central directory.

Ported from `reference/claude-code/src/utils/dxt/zip.ts` -> `LIMITS`, `isPathSafe`,
`validateZipFile`, `unzipFile`, `parseZipModes`, `readAndUnzipFile`.

Upstream unzips with fflate's `unzipSync`, whose `filter` sees each central-directory entry
(`name`, `originalSize`) before it is inflated; here `zipfile.ZipFile.infolist()` gives the same
entries in the same order, each checked before `read`. Directory entries (a trailing `/`) are
returned as empty data, as fflate returns them.
"""

from __future__ import annotations

import io
import logging
import math
import posixpath
import struct
import zipfile
from dataclasses import dataclass, field

from app.tools.bash_tool.path_validation import contains_path_traversal

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `LIMITS.MAX_FILE_SIZE`: 512MB per file.
MAX_FILE_SIZE = 512 * 1024 * 1024
#: `LIMITS.MAX_TOTAL_SIZE`: 1024MB total uncompressed.
MAX_TOTAL_SIZE = 1024 * 1024 * 1024
#: `LIMITS.MAX_FILE_COUNT`.
MAX_FILE_COUNT = 100000
#: `LIMITS.MAX_COMPRESSION_RATIO`: anything above 50:1 is suspicious.
MAX_COMPRESSION_RATIO = 50
#: `LIMITS.MIN_COMPRESSION_RATIO` (declared upstream, never checked).
MIN_COMPRESSION_RATIO = 0.5

_EOCD_SIGNATURE = 0x06054B50
_CENTRAL_SIGNATURE = 0x02014B50


@dataclass
class ZipValidationState:
    """`ZipValidationState`."""

    file_count: int = 0
    total_uncompressed_size: int = 0
    compressed_size: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FileValidationResult:
    """`FileValidationResult`."""

    is_valid: bool
    error: str | None = None


def _js_round(value: float) -> int:
    """`Math.round`: halves round up."""
    return math.floor(value + 0.5)


def is_path_safe(file_path: str) -> bool:
    """`isPathSafe`: no `..` segment, and relative once normalized."""
    if contains_path_traversal(file_path):
        return False
    return not posixpath.isabs(posixpath.normpath(file_path))


def validate_zip_file(name: str, original_size: int | None, state: ZipValidationState) -> FileValidationResult:
    """`validateZipFile`: count, path, size, total and ratio checks; the last failing check is reported."""
    state.file_count += 1
    error: str | None = None
    if state.file_count > MAX_FILE_COUNT:
        error = f"Archive contains too many files: {state.file_count} (max: {MAX_FILE_COUNT})"
    if not is_path_safe(name):
        error = f'Unsafe file path detected: "{name}". Path traversal or absolute paths are not allowed.'
    file_size = original_size or 0
    if file_size > MAX_FILE_SIZE:
        error = (
            f'File "{name}" is too large: {_js_round(file_size / 1024 / 1024)}MB '
            f"(max: {_js_round(MAX_FILE_SIZE / 1024 / 1024)}MB)"
        )
    state.total_uncompressed_size += file_size
    if state.total_uncompressed_size > MAX_TOTAL_SIZE:
        error = (
            f"Archive total size is too large: {_js_round(state.total_uncompressed_size / 1024 / 1024)}MB "
            f"(max: {_js_round(MAX_TOTAL_SIZE / 1024 / 1024)}MB)"
        )
    if state.compressed_size:
        current_ratio = state.total_uncompressed_size / state.compressed_size
    else:
        # JS division: x/0 is Infinity, 0/0 is NaN (and NaN > 50 is false).
        current_ratio = math.inf if state.total_uncompressed_size else math.nan
    if current_ratio > MAX_COMPRESSION_RATIO:
        error = (
            f"Suspicious compression ratio detected: {current_ratio:.1f}:1 "
            f"(max: {MAX_COMPRESSION_RATIO}:1). This may be a zip bomb."
        )
    return FileValidationResult(is_valid=False, error=error) if error else FileValidationResult(is_valid=True)


async def unzip_file(zip_data: bytes) -> dict[str, bytes]:
    """`unzipFile`: every entry by name, each validated before it is read; a failure raises."""
    state = ZipValidationState(compressed_size=len(zip_data))
    result: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
        for info in archive.infolist():
            validation = validate_zip_file(info.filename, info.file_size, state)
            if not validation.is_valid:
                raise ValueError(validation.error)
            result[info.filename] = b"" if info.filename.endswith("/") else archive.read(info)
    logger.debug(
        "Zip extraction completed: %d files, %dKB uncompressed",
        state.file_count,
        _js_round(state.total_uncompressed_size / 1024),
    )
    return result


def parse_zip_modes(data: bytes) -> dict[str, int]:
    """
    `parseZipModes`: `name -> st_mode` for the central-directory entries made on a Unix host
    (`versionMadeBy >> 8 == 3`) with mode bits set. A malformed archive yields `{}`; ZIP64 is
    not handled.
    """
    modes: dict[str, int] = {}
    min_eocd = max(0, len(data) - 22 - 0xFFFF)
    eocd = -1
    for i in range(len(data) - 22, min_eocd - 1, -1):
        if struct.unpack_from("<I", data, i)[0] == _EOCD_SIGNATURE:
            eocd = i
            break
    if eocd < 0:
        return modes
    entry_count = struct.unpack_from("<H", data, eocd + 10)[0]
    offset = struct.unpack_from("<I", data, eocd + 16)[0]
    for _ in range(entry_count):
        if offset + 46 > len(data) or struct.unpack_from("<I", data, offset)[0] != _CENTRAL_SIGNATURE:
            break
        version_made_by = struct.unpack_from("<H", data, offset + 4)[0]
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, offset + 28)
        external_attr = struct.unpack_from("<I", data, offset + 38)[0]
        name = data[offset + 46:offset + 46 + name_len].decode("utf-8", errors="replace")
        if version_made_by >> 8 == 3:
            mode = (external_attr >> 16) & 0xFFFF
            if mode:
                modes[name] = mode
        offset += 46 + name_len + extra_len + comment_len
    return modes


async def read_and_unzip_file(file_path: str) -> dict[str, bytes]:
    """`readAndUnzipFile`: a missing file raises as is; any other failure is wrapped."""
    try:
        with open(file_path, "rb") as handle:
            zip_data = handle.read()
        return await unzip_file(zip_data)
    except FileNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001 - upstream wraps every other failure
        raise RuntimeError(f"Failed to read or unzip file: {exc}") from exc


__all__ = [
    "MAX_COMPRESSION_RATIO",
    "MAX_FILE_COUNT",
    "MAX_FILE_SIZE",
    "MAX_TOTAL_SIZE",
    "MIN_COMPRESSION_RATIO",
    "FileValidationResult",
    "ZipValidationState",
    "is_path_safe",
    "parse_zip_modes",
    "read_and_unzip_file",
    "unzip_file",
    "validate_zip_file",
]
