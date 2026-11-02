"""
Shared error classes.

Ported from `reference/claude-code/src/utils/errors.ts` -> `ConfigParseError`.
"""

from __future__ import annotations

from typing import Any


class ConfigParseError(Exception):
    """`ConfigParseError`: a configuration file that failed to parse or validate."""

    def __init__(self, message: str, file_path: str, default_config: Any) -> None:
        super().__init__(message)
        self.file_path = file_path
        self.default_config = default_config


__all__ = ["ConfigParseError"]
