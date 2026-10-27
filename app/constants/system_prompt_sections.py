"""
System prompt section registry and boundary management.

Ported from `reference/claude-code/src/constants/systemPromptSections.ts` and `prompts.ts`:
- Two constructors: `systemPromptSection` (memoized) and
  `DANGEROUS_uncachedSystemPromptSection` (volatile with required reason).
- `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` sentinel separating static cacheable prose from dynamic session prose.
- Run-scoped section cache to isolate subagents and prevent cross-run cache pollution (§4.8).
- `clearSystemPromptSections` as `invalidate_context_caches`: the section cache and the
  memoized per-run context are cleared together after a compaction (§5 Part E8).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SystemPromptSection:
    """A named section of the system prompt."""

    name: str
    compute: Callable[[], str | None | Awaitable[str | None]]
    cache_break: bool = False
    reason: str | None = None


# Boundary sentinel marking the transition from globally cacheable static instructions
# to uncached, session-specific dynamic context.
SYSTEM_PROMPT_DYNAMIC_BOUNDARY = SystemPromptSection(
    name="SYSTEM_PROMPT_DYNAMIC_BOUNDARY",
    compute=lambda: None,
    cache_break=False,
)


def systemPromptSection(
    name: str,
    compute: Callable[[], str | None | Awaitable[str | None]],
) -> SystemPromptSection:
    """
    Define a memoized system prompt section.

    Computed once per run and reused across all subsequent turns until the run cache is cleared.
    """
    return SystemPromptSection(name=name, compute=compute, cache_break=False)


def DANGEROUS_uncachedSystemPromptSection(
    name: str,
    compute: Callable[[], str | None | Awaitable[str | None]],
    reason: str,
) -> SystemPromptSection:
    """
    Define a volatile system prompt section recomputed on every turn.

    Requires a non-empty `reason` parameter to enforce explicit justification
    for cache-busting behavior during review (§4.8).
    """
    if not reason or not reason.strip():
        raise ValueError(
            f"DANGEROUS_uncachedSystemPromptSection('{name}') requires a non-empty 'reason' argument."
        )
    return SystemPromptSection(
        name=name,
        compute=compute,
        cache_break=True,
        reason=reason.strip(),
    )


class PromptSectionCache:
    """Run-scoped cache for resolved prompt sections."""

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}

    def has(self, name: str) -> bool:
        return name in self._cache

    def __contains__(self, name: str) -> bool:
        return name in self._cache

    def get(self, name: str) -> str | None:
        return self._cache.get(name)

    def set(self, name: str, value: str | None) -> None:
        self._cache[name] = value

    def clear(self) -> None:
        self._cache.clear()


def split_system_prompt_sections(
    sections: Sequence[SystemPromptSection],
) -> tuple[list[SystemPromptSection], list[SystemPromptSection]]:
    """
    Split a list of sections into (static_sections, dynamic_sections)
    around the SYSTEM_PROMPT_DYNAMIC_BOUNDARY sentinel.
    """
    static_secs: list[SystemPromptSection] = []
    dynamic_secs: list[SystemPromptSection] = []
    found_boundary = False

    for s in sections:
        if s.name == SYSTEM_PROMPT_DYNAMIC_BOUNDARY.name:
            found_boundary = True
            continue
        if not found_boundary:
            static_secs.append(s)
        else:
            dynamic_secs.append(s)

    return static_secs, dynamic_secs


def resolve_system_prompt_sections(
    sections: Sequence[SystemPromptSection],
    cache: PromptSectionCache | None = None,
) -> list[str]:
    """
    Resolve system prompt sections into concrete string blocks.

    None is treated as a cached decision (omitted from output, not a cache miss).
    """
    effective_cache = cache if cache is not None else PromptSectionCache()
    rendered: list[str] = []

    for s in sections:
        if s.name == SYSTEM_PROMPT_DYNAMIC_BOUNDARY.name:
            continue

        # Check cache if not volatile
        if not s.cache_break and effective_cache.has(s.name):
            cached_val = effective_cache.get(s.name)
            if cached_val is not None and cached_val.strip():
                rendered.append(cached_val.strip())
            continue

        # Compute
        val = s.compute()
        if inspect.isawaitable(val):
            if inspect.iscoroutine(val):
                val.close()
            # If called synchronously but returned a coroutine, raise or handle
            raise RuntimeError(
                f"Section '{s.name}' returned an awaitable in synchronous resolve. "
                "Use resolve_system_prompt_sections_async."
            )

        str_val: str | None = val if isinstance(val, str) else None
        effective_cache.set(s.name, str_val)

        if str_val is not None and str_val.strip():
            rendered.append(str_val.strip())

    return rendered


async def resolve_system_prompt_sections_async(
    sections: Sequence[SystemPromptSection],
    cache: PromptSectionCache | None = None,
) -> list[str]:
    """Asynchronous variant of section resolution supporting async compute functions."""
    effective_cache = cache if cache is not None else PromptSectionCache()
    rendered: list[str] = []

    for s in sections:
        if s.name == SYSTEM_PROMPT_DYNAMIC_BOUNDARY.name:
            continue

        if not s.cache_break and effective_cache.has(s.name):
            cached_val = effective_cache.get(s.name)
            if cached_val is not None and cached_val.strip():
                rendered.append(cached_val.strip())
            continue

        res = s.compute()
        val = await res if inspect.isawaitable(res) else res

        str_val: str | None = val if isinstance(val, str) else None
        effective_cache.set(s.name, str_val)

        if str_val is not None and str_val.strip():
            rendered.append(str_val.strip())

    return rendered


def invalidate_context_caches(
    section_cache: PromptSectionCache | None = None,
    run_id: str | None = None,
) -> None:
    """
    Clear context caches at a named lifecycle point (post-compaction or session clear).

    Both layers go together (§5 Part E8): the section cache, and the memoized per-run
    context that wraps its own copy, so no caller observes a stale section.
    """
    if section_cache is not None:
        section_cache.clear()
    if run_id is not None:
        # Upstream clears getUserContext/getMemoryFiles after compaction; the memoized
        # per-run context wraps its own section cache, so both layers go together.
        from app.context import reset_session_context

        reset_session_context(run_id)
