"""
Function hooks: session-scoped hooks that are code, not settings.

Ported from `reference/claude-code/src/utils/hooks/sessionHooks.ts` -> `addFunctionHook`,
`removeFunctionHook` (and `FunctionHook`, in `app/schemas/hooks.py`). Upstream keeps them in
`AppState.sessionHooks` keyed by session id; here a run's session-scoped matchers live on its hook
dispatcher (`HookDispatcher.session_matchers`, beside an agent's or skill's frontmatter hooks), so
they are scoped to the run that added them. A function hook defaults to upstream's 5 s timeout.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from typing import Any

from app.schemas.hooks import FunctionHook, HookMatcher

#: `addFunctionHook`'s default `timeout` (milliseconds).
FUNCTION_HOOK_DEFAULT_TIMEOUT_MS = 5000


def add_function_hook(
    dispatcher: Any, event: str, matcher: str, callback: Callable[[Sequence[Any]], Any], error_message: str,
    *, timeout: float | None = None, hook_id: str | None = None,
) -> str:
    """`addFunctionHook`: register `callback` for `event`; returns the hook's id."""
    hook_id = hook_id or f"function-hook-{int(time.time() * 1000)}-{random.random()}"
    hook = FunctionHook(callback=callback, error_message=error_message, id=hook_id,
                        timeout=timeout or FUNCTION_HOOK_DEFAULT_TIMEOUT_MS)
    dispatcher.session_matchers.setdefault(event, []).append(HookMatcher(hooks=(hook,), matcher=matcher or None))
    return hook_id


def remove_function_hook(dispatcher: Any, event: str, hook_id: str) -> None:
    """`removeFunctionHook`: drop the hook with `hook_id`, and any matcher left empty."""
    kept: list[HookMatcher] = []
    for matcher in dispatcher.session_matchers.get(event, []):
        hooks = tuple(h for h in matcher.hooks if not (isinstance(h, FunctionHook) and h.id == hook_id))
        if hooks:
            kept.append(HookMatcher(hooks=hooks, matcher=matcher.matcher))
    if kept:
        dispatcher.session_matchers[event] = kept
    else:
        dispatcher.session_matchers.pop(event, None)


__all__ = ["FUNCTION_HOOK_DEFAULT_TIMEOUT_MS", "add_function_hook", "remove_function_hook"]
