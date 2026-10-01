"""
Tests for Part E4b: Section registry, memoized/volatile split, boundary (app/loop/prompts/sections.py).
"""

import asyncio
import pytest

from app.loop.prompts.sections import (
    DANGEROUS_uncachedSystemPromptSection,
    PromptSectionCache,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    resolve_system_prompt_sections,
    resolve_system_prompt_sections_async,
    split_system_prompt_sections,
    systemPromptSection,
)


def test_system_prompt_section_memoized() -> None:
    call_count = 0

    def compute_static() -> str:
        nonlocal call_count
        call_count += 1
        return "Static content"

    section = systemPromptSection("identity", compute_static)
    assert section.name == "identity"
    assert section.cache_break is False
    assert section.reason is None

    cache = PromptSectionCache()

    # First resolve computes and stores
    res1 = resolve_system_prompt_sections([section], cache)
    assert res1 == ["Static content"]
    assert call_count == 1

    # Second resolve uses cache without calling compute again
    res2 = resolve_system_prompt_sections([section], cache)
    assert res2 == ["Static content"]
    assert call_count == 1  # Did not increment!


def test_dangerous_uncached_section_recomputes_every_turn() -> None:
    call_count = 0

    def compute_volatile() -> str:
        nonlocal call_count
        call_count += 1
        return f"Volatile count {call_count}"

    section = DANGEROUS_uncachedSystemPromptSection(
        "mcp_instructions",
        compute_volatile,
        reason="MCP servers connect and disconnect between turns",
    )
    assert section.cache_break is True
    assert section.reason == "MCP servers connect and disconnect between turns"

    cache = PromptSectionCache()

    res1 = resolve_system_prompt_sections([section], cache)
    assert res1 == ["Volatile count 1"]
    assert call_count == 1

    res2 = resolve_system_prompt_sections([section], cache)
    assert res2 == ["Volatile count 2"]
    assert call_count == 2  # Recomputed!


def test_dangerous_uncached_section_requires_reason() -> None:
    with pytest.raises(ValueError):
        DANGEROUS_uncachedSystemPromptSection(
            "test",
            lambda: "val",
            reason="",  # Empty reason must raise
        )
    with pytest.raises(ValueError):
        DANGEROUS_uncachedSystemPromptSection(
            "test",
            lambda: "val",
            reason="   ",  # Whitespace-only reason must raise
        )


def test_none_is_a_cached_decision_not_a_miss() -> None:
    call_count = 0

    def compute_none() -> str | None:
        nonlocal call_count
        call_count += 1
        return None

    section = systemPromptSection("optional_block", compute_none)
    cache = PromptSectionCache()

    res1 = resolve_system_prompt_sections([section], cache)
    assert res1 == []  # None is omitted from results
    assert call_count == 1
    assert "optional_block" in cache  # Cached as None

    res2 = resolve_system_prompt_sections([section], cache)
    assert res2 == []
    assert call_count == 1  # Not re-run!


def test_split_system_prompt_sections_around_boundary() -> None:
    s1 = systemPromptSection("s1", lambda: "static 1")
    s2 = systemPromptSection("s2", lambda: "static 2")
    d1 = systemPromptSection("d1", lambda: "dynamic 1")

    sections = [s1, s2, SYSTEM_PROMPT_DYNAMIC_BOUNDARY, d1]
    static_secs, dynamic_secs = split_system_prompt_sections(sections)

    assert [s.name for s in static_secs] == ["s1", "s2"]
    assert [s.name for s in dynamic_secs] == ["d1"]


def test_run_scoped_cache_isolation() -> None:
    def compute() -> str:
        return "val"

    sec = systemPromptSection("sec", compute)

    cache_parent = PromptSectionCache()
    cache_child = PromptSectionCache()

    resolve_system_prompt_sections([sec], cache_parent)
    assert "sec" in cache_parent
    assert "sec" not in cache_child
    assert cache_parent.get("sec") == "val"
    assert cache_child.get("sec") is None

    cache_parent.clear()
    assert not cache_parent.has("sec")


def test_resolve_sync_with_awaitable_raises_runtime_error() -> None:
    async def async_compute() -> str:
        return "async_val"

    sec = systemPromptSection("async_sec", async_compute)
    with pytest.raises(RuntimeError) as exc_info:
        resolve_system_prompt_sections([sec])
    assert "async_sec" in str(exc_info.value)


def test_resolve_system_prompt_sections_async() -> None:
    async def _test() -> None:
        call_count = 0

        async def async_compute() -> str:
            nonlocal call_count
            call_count += 1
            return "async_content"

        def sync_compute() -> str:
            return "sync_content"

        def empty_compute() -> str | None:
            return None

        sec_async = systemPromptSection("async_sec", async_compute)
        sec_sync = systemPromptSection("sync_sec", sync_compute)
        sec_empty = systemPromptSection("empty_sec", empty_compute)

        sections = [
            sec_async,
            SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
            sec_sync,
            sec_empty,
        ]

        cache = PromptSectionCache()

        # First call computes
        res1 = await resolve_system_prompt_sections_async(sections, cache)
        assert res1 == ["async_content", "sync_content"]
        assert call_count == 1
        assert "async_sec" in cache
        assert "sync_sec" in cache
        assert "empty_sec" in cache

        # Second call hits cache
        res2 = await resolve_system_prompt_sections_async(sections, cache)
        assert res2 == ["async_content", "sync_content"]
        assert call_count == 1

        # Volatile section with async resolve
        volatile_calls = 0

        async def volatile_compute() -> str:
            nonlocal volatile_calls
            volatile_calls += 1
            return f"volatile_{volatile_calls}"

        sec_volatile = DANGEROUS_uncachedSystemPromptSection(
            "volatile_sec",
            volatile_compute,
            reason="testing volatile async",
        )
        res3 = await resolve_system_prompt_sections_async([sec_volatile], cache)
        assert res3 == ["volatile_1"]
        res4 = await resolve_system_prompt_sections_async([sec_volatile], cache)
        assert res4 == ["volatile_2"]
        assert volatile_calls == 2

    asyncio.run(_test())


def test_sync_resolve_boundary_and_cached_continuation() -> None:
    s1 = systemPromptSection("s1", lambda: "c1")
    s2 = systemPromptSection("s2", lambda: "c2")

    # Boundary should be skipped via continue, resolving s2
    res_boundary = resolve_system_prompt_sections([s1, SYSTEM_PROMPT_DYNAMIC_BOUNDARY, s2])
    assert res_boundary == ["c1", "c2"]

    # Cached items should be skipped via continue, resolving both
    cache = PromptSectionCache()
    cache.set("s1", "cached_1")
    cache.set("s2", "cached_2")
    res_cached = resolve_system_prompt_sections([s1, s2], cache)
    assert res_cached == ["cached_1", "cached_2"]


def test_section_constructors() -> None:
    # Plain section
    plain = systemPromptSection("plain_sec", lambda: "text")
    assert plain.name == "plain_sec"
    assert plain.cache_break is False

    # Volatile section requiring non-empty reason
    with pytest.raises(ValueError) as exc1:
        DANGEROUS_uncachedSystemPromptSection("bad_sec", lambda: "text", reason="")
    assert "DANGEROUS_uncachedSystemPromptSection('bad_sec')" in str(exc1.value)
    assert "requires a non-empty 'reason' argument" in str(exc1.value)

    with pytest.raises(ValueError) as exc2:
        DANGEROUS_uncachedSystemPromptSection("bad_sec2", lambda: "text", reason="   ")
    assert "requires a non-empty 'reason' argument" in str(exc2.value)

    good = DANGEROUS_uncachedSystemPromptSection("good_sec", lambda: "text", reason="valid reason")
    assert good.name == "good_sec"
    assert good.cache_break is True
