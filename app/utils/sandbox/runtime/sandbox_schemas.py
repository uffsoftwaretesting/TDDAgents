"""
The runtime's internal restriction structures.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.46 `src/sandbox/sandbox-schemas.ts` ->
`FsReadRestrictionConfig`, `FsWriteRestrictionConfig`, `NetworkRestrictionConfig`,
`NetworkHostPattern`, `SandboxAskCallback`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypedDict


@dataclass(frozen=True)
class FsReadRestrictionConfig:
    """
    `FsReadRestrictionConfig`, "deny then allow-back": reads are denied under `deny_only`, except within
    `allow_within_deny` (the most specific rule wins). An empty `deny_only` restricts nothing.
    """

    deny_only: list[str]
    allow_within_deny: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FsWriteRestrictionConfig:
    """
    `FsWriteRestrictionConfig`, "allow-only": writes are allowed under `allow_only` only, except
    within `deny_within_allow`. An empty `allow_only` means nothing is writable.
    """

    allow_only: list[str]
    deny_within_allow: list[str]


@dataclass(frozen=True)
class NetworkRestrictionConfig:
    """`NetworkRestrictionConfig`: the allowed and the denied hosts (denied ones checked first)."""

    allowed_hosts: list[str] | None = None
    denied_hosts: list[str] | None = None


class NetworkHostPattern(TypedDict):
    """`NetworkHostPattern`: the host a sandboxed command asked to reach, and its port."""

    host: str
    port: int | None


#: `SandboxAskCallback`: asked for a host no rule allows or denies; `True` lets the connection through.
SandboxAskCallback = Callable[[NetworkHostPattern], Awaitable[bool]]


__all__ = [
    "FsReadRestrictionConfig",
    "FsWriteRestrictionConfig",
    "NetworkHostPattern",
    "NetworkRestrictionConfig",
    "SandboxAskCallback",
]
