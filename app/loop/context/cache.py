"""
Cache breakpoints management and session latching rule.

Ported from:
- `reference/claude-code/src/services/api/claude.ts` -> `buildSystemPromptBlocks`, `getCacheControl`
- `reference/claude-code/src/constants/prompts.ts` -> cache latching and breakpoint budget.

Hard constraints:
- API breakpoint budget: at most 4 cache_control markers across system blocks and messages.
- Latch rule: anything feeding the prefix cache key must be latched for the session (Rule E5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from app.loop.prompts.sections import (
    PromptSectionCache,
    SystemPromptSection,
    resolve_system_prompt_sections,
    split_system_prompt_sections,
)

MAX_CACHE_BREAKPOINTS = 4


class CacheLatchError(RuntimeError):
    """Raised when a session-latched value attempts to change mid-session."""


@dataclass(frozen=True, slots=True)
class CacheBlock:
    """A formatted text block for the Messages API, optionally carrying cache_control."""

    text: str
    cache_control: dict[str, str] | None = None


class SessionLatches:
    """
    Session-level latches to preserve cache stability.

    Rule E5: Any feature flag, tool hash, or configuration bit that feeds
    the prefix cache key must be latched for the entire session. A value that flips
    mid-run silently busts the prompt cache.
    """

    def __init__(self) -> None:
        self._latches: dict[str, Any] = {}

    def has(self, key: str) -> bool:
        return key in self._latches

    def get(self, key: str, default: Any = None) -> Any:
        return self._latches.get(key, default)

    def latch(self, key: str, value: Any) -> None:
        if key in self._latches:
            existing = self._latches[key]
            if existing != value:
                raise CacheLatchError(
                    f"Cache key '{key}' was latched as {existing!r} but attempted to change to {value!r}. "
                    "Flipping cache keys mid-session busts prefix cache (Rule E5)."
                )
            return
        self._latches[key] = value

    def get_or_latch(self, key: str, factory: Callable[[], Any]) -> Any:
        if key not in self._latches:
            self._latches[key] = factory()
        return self._latches[key]

    def clear(self) -> None:
        """Clear latches at named session reset points."""
        self._latches.clear()


def validate_cache_breakpoints_budget(blocks: Sequence[CacheBlock]) -> tuple[bool, int]:
    """
    Validate that total cache_control breakpoints do not exceed the API budget of 4.

    Returns (is_valid, count_of_breakpoints).
    """
    count = sum(1 for b in blocks if b.cache_control is not None)
    return count <= MAX_CACHE_BREAKPOINTS, count


def build_system_prompt_blocks(
    sections: Sequence[SystemPromptSection],
    cache: PromptSectionCache | None = None,
    attribution_header: str | None = None,
    enable_prompt_caching: bool = True,
) -> list[CacheBlock]:
    """
    Assemble system prompt sections into structured CacheBlocks.

    Places ephemeral cache_control only on the concatenated static section block
    (Block 3), while attribution and dynamic sections remain uncached.
    """
    blocks: list[CacheBlock] = []

    # Block 1: Attribution / billing header (uncached)
    if attribution_header and attribution_header.strip():
        blocks.append(CacheBlock(text=attribution_header.strip(), cache_control=None))

    static_secs, dynamic_secs = split_system_prompt_sections(sections)

    # Block 3: Static sections concatenated (cached globally)
    static_resolved = resolve_system_prompt_sections(static_secs, cache)
    if static_resolved:
        static_text = "\n\n".join(static_resolved)
        cache_ctrl = {"type": "ephemeral"} if enable_prompt_caching else None
        blocks.append(CacheBlock(text=static_text, cache_control=cache_ctrl))

    # Block 4: Dynamic sections concatenated (uncached)
    dynamic_resolved = resolve_system_prompt_sections(dynamic_secs, cache)
    if dynamic_resolved:
        dynamic_text = "\n\n".join(dynamic_resolved)
        blocks.append(CacheBlock(text=dynamic_text, cache_control=None))

    return blocks
