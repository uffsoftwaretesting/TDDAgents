"""
The secure-storage contract: what a credential store reads, writes and deletes.

Ported from `reference/claude-code/src/utils/secureStorage/types.ts` -> `SecureStorage`,
`SecureStorageData`. The module exports only types, so it is erased from the source snapshot;
its shape is read off its implementations (`plainTextStorage.ts`, `fallbackStorage.ts`,
`macOsKeychainStorage.ts`) and its callers (`services/mcp/auth.ts`).

`SecureStorageData` is the JSON document the store holds. The MCP OAuth slots are
`mcpOAuth[<server key>]` (tokens, client registration, discovery state, step-up scope) and
`mcpOAuthClientConfig[<server key>]` (a pre-configured client secret). Upstream's other
top-level keys (the claude.ai account, the XAA IdP slots) are left untouched by every writer
here: each update spreads the document it read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

#: `SecureStorageData`: a JSON object.
SecureStorageData = dict[str, Any]


@dataclass(frozen=True)
class SecureStorageUpdateResult:
    """`update()`'s `{ success, warning? }`."""

    success: bool
    warning: str | None = None


class SecureStorage(Protocol):
    """`SecureStorage`."""

    name: str

    def read(self) -> SecureStorageData | None:
        """The stored document, or `None` when there is none (or it cannot be read)."""
        ...

    async def read_async(self) -> SecureStorageData | None:
        """`readAsync`."""
        ...

    def update(self, data: SecureStorageData) -> SecureStorageUpdateResult:
        """Replace the stored document with `data`."""
        ...

    def delete(self) -> bool:
        """Remove the stored document; `True` when nothing is left."""
        ...


__all__ = ["SecureStorage", "SecureStorageData", "SecureStorageUpdateResult"]
