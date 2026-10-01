"""
Post-compact cleanup and cache invalidation at named points.

Ported from `reference/claude-code/src/constants/systemPromptSections.ts` -> `clearSystemPromptSections`:
- Named invalidation points: /clear (session reset) and /compact (post-compaction).
- Avoids the two-layer cache trap (clearing inner memoized cache and outer latch wrapper together).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.loop.context.cache import SessionLatches
from app.loop.prompts.sections import PromptSectionCache

if TYPE_CHECKING:
    from app.loop.state import CompactionTracking


def invalidate_context_caches(
    section_cache: PromptSectionCache | None = None,
    session_latches: SessionLatches | None = None,
) -> None:
    """
    Clear context caches at a named lifecycle point (post-compaction or session clear).

    Avoids the two-layer cache trap (§5 Part E8): clearing the inner section cache
    while leaving outer session latches untouched causes the caller to observe stale
    cached state.
    """
    if section_cache is not None:
        section_cache.clear()
    if session_latches is not None:
        session_latches.clear()


def notify_compaction(
    turn_id: str = "compaction",
    turn_counter: int = 0,
) -> CompactionTracking:
    """Return a fresh CompactionTracking record after compaction runs."""
    from app.loop.state import CompactionTracking

    return CompactionTracking(
        compacted=True,
        turn_id=turn_id,
        turn_counter=turn_counter,
    )
