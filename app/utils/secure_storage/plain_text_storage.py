"""
The plaintext credential store: one JSON file, mode 0600, in the config home.

Ported from `reference/claude-code/src/utils/secureStorage/plainTextStorage.ts` ->
`plainTextStorage` (`getStoragePath`, `read`, `readAsync`, `update`, `delete`). The file is
`<config home>/.credentials.json`; the config home is TDDAgents' (`TDDAGENTS_CONFIG_DIR`,
default `~/.tddagents`), upstream's `getClaudeConfigHomeDir`.

`read` answers `None` for a missing or unparsable file, as upstream's `catch { return null }`.
`update` creates the directory, writes the JSON and chmods the file to 0600, answering
`success` with upstream's plaintext warning; any failure answers `success: False`. `delete`
treats a missing file as deleted.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from app.utils.secure_storage.types import SecureStorageData, SecureStorageUpdateResult
from app.utils.tool_result_storage import config_home

STORAGE_FILE_NAME = ".credentials.json"
PLAINTEXT_WARNING = "Warning: Storing credentials in plaintext."


def get_storage_path() -> tuple[Path, Path]:
    """`getStoragePath`: `(storage dir, storage path)`."""
    storage_dir = config_home()
    return storage_dir, storage_dir / STORAGE_FILE_NAME


def _parse(text: str) -> SecureStorageData | None:
    data = json.loads(text)
    return data if isinstance(data, dict) else None


class PlainTextStorage:
    """`plainTextStorage`."""

    name = "plaintext"

    def read(self) -> SecureStorageData | None:
        _, storage_path = get_storage_path()
        try:
            return _parse(storage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    async def read_async(self) -> SecureStorageData | None:
        return await asyncio.to_thread(self.read)

    def update(self, data: SecureStorageData) -> SecureStorageUpdateResult:
        try:
            storage_dir, storage_path = get_storage_path()
            storage_dir.mkdir(exist_ok=True)
            storage_path.write_text(json.dumps(data), encoding="utf-8")
            os.chmod(storage_path, 0o600)
            return SecureStorageUpdateResult(success=True, warning=PLAINTEXT_WARNING)
        except (OSError, TypeError, ValueError):
            return SecureStorageUpdateResult(success=False)

    def delete(self) -> bool:
        _, storage_path = get_storage_path()
        try:
            storage_path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False


plain_text_storage = PlainTextStorage()

__all__ = [
    "PLAINTEXT_WARNING",
    "STORAGE_FILE_NAME",
    "PlainTextStorage",
    "get_storage_path",
    "plain_text_storage",
]
