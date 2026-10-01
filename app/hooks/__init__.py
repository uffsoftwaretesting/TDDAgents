"""
Hooks system: middleware, lifecycle events, and TDD enforcement (Part H).

Ported from:
- `reference/claude-code/src/utils/hooks.ts`
- `reference/claude-code/src/schemas/hooks.ts`
- `reference/claude-code/src/query/stopHooks.ts`
"""

from __future__ import annotations

from app.hooks.backends import (
    BLOCKING_EXIT_CODE,
    AgentHookBackend,
    CommandHookBackend,
    HookExecutionOutcome,
    HttpHookBackend,
    PromptHookBackend,
)
from app.hooks.config import (
    DEFAULT_HOOK_TIMEOUT,
    DEFAULT_PROMPT_TIMEOUT,
    KNOWN_EVENTS,
    LOCAL_SETTINGS_FILENAME,
    SETTINGS_DIR,
    SETTINGS_FILENAME,
    TOOL_HOOK_EVENTS,
    load_hook_settings,
    settings_paths,
)
from app.hooks.dispatcher import HookDispatcher, HookOutcome
from app.hooks.events import (
    HOOK_EVENTS,
    HookEvent,
    extract_match_key,
    matches_pattern,
)
from app.hooks.matching import deduplicate_hooks, eval_if_condition
from app.hooks.schemas import (
    AgentHook,
    CommandHook,
    HookCommand,
    HookDefinition,
    HookMatcher,
    HookSettings,
    HookType,
    HttpHook,
    PromptHook,
    parse_hook,
    parse_matcher,
)

__all__ = [
    "BLOCKING_EXIT_CODE",
    "DEFAULT_HOOK_TIMEOUT",
    "DEFAULT_PROMPT_TIMEOUT",
    "HOOK_EVENTS",
    "HookEvent",
    "KNOWN_EVENTS",
    "TOOL_HOOK_EVENTS",
    "SETTINGS_DIR",
    "SETTINGS_FILENAME",
    "LOCAL_SETTINGS_FILENAME",
    "HookType",
    "HookDefinition",
    "CommandHook",
    "HookCommand",
    "PromptHook",
    "AgentHook",
    "HttpHook",
    "HookMatcher",
    "HookSettings",
    "HookOutcome",
    "HookExecutionOutcome",
    "HookDispatcher",
    "CommandHookBackend",
    "PromptHookBackend",
    "AgentHookBackend",
    "HttpHookBackend",
    "extract_match_key",
    "matches_pattern",
    "eval_if_condition",
    "deduplicate_hooks",
    "load_hook_settings",
    "settings_paths",
    "parse_hook",
    "parse_matcher",
]
