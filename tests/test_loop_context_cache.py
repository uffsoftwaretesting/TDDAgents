"""
Tests for Part E5: Cache breakpoints and the latch rule (app/loop/context/cache.py).
"""

import pytest

from app.loop.context.cache import (
    CacheBlock,
    CacheLatchError,
    SessionLatches,
    build_system_prompt_blocks,
    validate_cache_breakpoints_budget,
)
from app.loop.prompts.sections import (
    PromptSectionCache,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    systemPromptSection,
)


def test_session_latches_get_or_latch() -> None:
    latches = SessionLatches()
    call_count = 0

    def compute_val() -> str:
        nonlocal call_count
        call_count += 1
        return "hash_123"

    v1 = latches.get_or_latch("tool_pool_hash", compute_val)
    assert v1 == "hash_123"
    assert call_count == 1

    # Second call returns latched value without re-running factory
    v2 = latches.get_or_latch("tool_pool_hash", compute_val)
    assert v2 == "hash_123"
    assert call_count == 1


def test_session_latches_flip_raises_latch_error() -> None:
    latches = SessionLatches()
    latches.latch("feature_x", True)

    # Re-latching same value is safe
    latches.latch("feature_x", True)

    # Flipping value mid-session raises CacheLatchError
    with pytest.raises(CacheLatchError) as exc_info:
        latches.latch("feature_x", False)
    assert str(exc_info.value) == (
        "Cache key 'feature_x' was latched as True but attempted to change to False. "
        "Flipping cache keys mid-session busts prefix cache (Rule E5)."
    )


def test_build_system_prompt_blocks() -> None:
    s1 = systemPromptSection("identity", lambda: "I am Claude Code / TDDAgents.")
    s2 = systemPromptSection("contract", lambda: "Red then Green.")
    d1 = systemPromptSection("env", lambda: "Python 3.12, cwd=/workspace")

    sections = [s1, s2, SYSTEM_PROMPT_DYNAMIC_BOUNDARY, d1]
    cache = PromptSectionCache()

    blocks = build_system_prompt_blocks(
        sections,
        cache=cache,
        attribution_header="x-anthropic-billing-header: test",
    )

    assert len(blocks) == 3
    # Block 1: attribution (uncached)
    assert blocks[0].text == "x-anthropic-billing-header: test"
    assert blocks[0].cache_control is None

    # Block 2: static concatenated with ephemeral cache_control
    assert blocks[1].text == "I am Claude Code / TDDAgents.\n\nRed then Green."
    assert blocks[1].cache_control == {"type": "ephemeral"}

    # Block 3: dynamic (uncached)
    assert blocks[2].text == "Python 3.12, cwd=/workspace"
    assert blocks[2].cache_control is None

    # Verify cache got populated with static and dynamic memoized sections
    assert cache.get("identity") == "I am Claude Code / TDDAgents."
    assert cache.get("contract") == "Red then Green."
    assert cache.get("env") == "Python 3.12, cwd=/workspace"

    # Default enable_prompt_caching is True
    blocks_default = build_system_prompt_blocks(sections)
    assert blocks_default[0].cache_control == {"type": "ephemeral"}


def test_validate_cache_breakpoints_budget_max_four() -> None:

    # Under limit (3 breakpoints)
    blocks = [
        CacheBlock(text="b1", cache_control={"type": "ephemeral"}),
        CacheBlock(text="b2", cache_control={"type": "ephemeral"}),
        CacheBlock(text="b3", cache_control={"type": "ephemeral"}),
        CacheBlock(text="b4", cache_control=None),
    ]
    valid, count = validate_cache_breakpoints_budget(blocks)
    assert valid is True
    assert count == 3

    # Exactly at limit (4 breakpoints)
    exact_four = [
        CacheBlock(text=f"b{i}", cache_control={"type": "ephemeral"})
        for i in range(4)
    ]
    valid_exact, count_exact = validate_cache_breakpoints_budget(exact_four)
    assert valid_exact is True
    assert count_exact == 4

    # Exceeding budget (>4 breakpoints)
    too_many = [
        CacheBlock(text=f"b{i}", cache_control={"type": "ephemeral"})
        for i in range(5)
    ]
    valid, count = validate_cache_breakpoints_budget(too_many)
    assert valid is False
    assert count == 5


def test_session_latches_get_has_clear_all() -> None:
    latches = SessionLatches()
    assert not latches.has("foo")
    assert latches.get("foo") is None
    assert latches.get("foo", "fallback") == "fallback"

    latches.latch("foo", "bar")
    assert latches.has("foo")
    assert latches.get("foo") == "bar"

    latches.clear()
    assert not latches.has("foo")
    assert latches.get("foo") is None


def test_build_system_prompt_blocks_options() -> None:
    s1 = systemPromptSection("s1", lambda: "sec 1")
    s2 = systemPromptSection("s2", lambda: "sec 2")
    sections = [s1, s2]

    # Without caching enabled
    blocks_nocache = build_system_prompt_blocks(sections, enable_prompt_caching=False)
    assert len(blocks_nocache) == 1
    assert blocks_nocache[0].cache_control is None
    assert blocks_nocache[0].text == "sec 1\n\nsec 2"

    # With whitespace-only attribution header
    blocks_ws = build_system_prompt_blocks(sections, attribution_header="   \n  ")
    assert len(blocks_ws) == 1
    assert blocks_ws[0].cache_control == {"type": "ephemeral"}

    # Dynamic block exact joining
    d1 = systemPromptSection("d1", lambda: "dyn 1")
    d2 = systemPromptSection("d2", lambda: "dyn 2")
    blocks_dyn = build_system_prompt_blocks([SYSTEM_PROMPT_DYNAMIC_BOUNDARY, d1, d2])
    assert len(blocks_dyn) == 1
    assert blocks_dyn[0].cache_control is None
    assert blocks_dyn[0].text == "dyn 1\n\ndyn 2"
