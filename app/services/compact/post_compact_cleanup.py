"""
What a compaction makes stale.

Ported from `reference/claude-code/src/services/compact/postCompactCleanup.ts` ->
`runPostCompactCleanup`. A main-thread compaction (no `querySource`, a `repl_main_thread*`
source or `sdk`) clears the memoized user context and memory files as well as the system
prompt sections; a subagent's compaction clears only the sections, because the user context
is shared with the main thread. Here the main thread is a context with no `agent_id` (and no
query source naming another thread), and both memoized layers live in the run's
`SessionContext`, so a main-thread compaction resets it whole (`invalidate_context_caches`).

Not ported, for want of the subsystem: microcompact state (the time-based path keeps none;
cached microcompact is feature-gated), context collapse (feature-gated), classifier approval
and speculative-check caches, beta tracing, attribution and the session-messages cache.
"""

from __future__ import annotations

from typing import Any

from app.constants.system_prompt_sections import invalidate_context_caches
from app.context import get_session_context


def _is_main_thread_compact(context: Any, query_source: str | None) -> bool:
    if query_source is not None:
        return query_source.startswith("repl_main_thread") or query_source == "sdk"
    return getattr(context, "agent_id", None) is None


def run_post_compact_cleanup(context: Any, *, query_source: str | None = None) -> None:
    """`runPostCompactCleanup`."""
    run_id = getattr(context, "run_id", "") or ""
    if not run_id:
        return
    if _is_main_thread_compact(context, query_source):
        invalidate_context_caches(run_id=run_id)
        return
    session = get_session_context(run_id, getattr(context, "workspace", None))
    invalidate_context_caches(section_cache=session.section_cache)


__all__ = ["run_post_compact_cleanup"]
