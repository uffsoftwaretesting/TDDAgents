"""
Which credential store this platform uses.

Ported from `reference/claude-code/src/utils/secureStorage/index.ts` -> `getSecureStorage`,
and `macOsKeychainHelpers.ts` -> `clearKeychainCache`.

Upstream answers the plaintext store everywhere except macOS, where the Keychain comes first
and the plaintext file is its fallback (`createFallbackStorage(macOsKeychainStorage,
plainTextStorage)`); Linux has a `TODO: add libsecret support`. TDDAgents answers the plaintext
store on every platform: the macOS Keychain store (`macOsKeychainStorage.ts`, which drives the
`security` CLI, its read cache and prefetch) and the fallback composition are not ported.
`clear_keychain_cache` is therefore a no-op kept so callers read as upstream's do: the
plaintext store has no cache to invalidate.
"""

from __future__ import annotations

from app.utils.secure_storage.plain_text_storage import plain_text_storage
from app.utils.secure_storage.types import SecureStorage


def get_secure_storage() -> SecureStorage:
    """`getSecureStorage`: the plaintext store (upstream's Linux answer)."""
    return plain_text_storage


def clear_keychain_cache() -> None:
    """`clearKeychainCache`: nothing to clear without the macOS Keychain store."""


__all__ = ["clear_keychain_cache", "get_secure_storage"]
