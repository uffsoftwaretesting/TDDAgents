"""
Which configured hooks run for one event.

Ported from `reference/claude-code/src/utils/hooks.ts` -> `matchesPattern`,
`prepareIfConditionMatcher`, `hookDedupKey`, `getHooksConfig`, `getMatchingHooks`.

* **Matcher.** Empty or `*` matches everything; letters, digits, `_` and `|` are exact names
  (a `|` list is a set of names), legacy tool names normalised; anything else is a regex,
  also tried against the legacy names of the query. An invalid regex matches nothing.
* **No match query** (Stop, UserPromptSubmit, ...): every matcher applies.
* **Dedup.** Command hooks by `(shell, command, if)`, prompt and agent hooks by
  `(prompt, if)`, HTTP hooks by `(url, if)`; the *last* duplicate wins (upstream builds a
  `Map`, which keeps the last entry). Order is then commands, prompts, agents, HTTP.
* **`if` conditions** are permission-rule syntax (`Bash(git *)`) and only make sense for a
  tool event; on any other event a hook with an `if` is skipped. The tool's
  `prepare_permission_matcher` decides content matches; a tool without one, or an input that
  fails the tool's schema, matches only a bare `Tool` condition.
* **HTTP hooks never run for SessionStart or Setup** (upstream: the headless sandbox ask
  callback would deadlock).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.loop.hooks.config import HooksConfigSnapshot
from app.loop.hooks.events import TOOL_EVENTS, HookEvent, match_query_for
from app.loop.hooks.schemas import (
    DEFAULT_HOOK_SHELL,
    CommandHook,
    HookCommand,
    HookMatcher,
    HttpHook,
)
from app.loop.permissions.settings import (
    LEGACY_TOOL_NAME_ALIASES,
    normalize_legacy_tool_name,
    permission_rule_value_from_string,
)

logger = logging.getLogger("TDDOrchestrator.Hooks")

_SIMPLE_MATCHER = re.compile(r"^[a-zA-Z0-9_|]+$")

IfConditionMatcher = Callable[[str], bool]


def get_legacy_tool_names(name: str) -> list[str]:
    """`getLegacyToolNames`: the old names that now mean `name`."""
    return [legacy for legacy, current in LEGACY_TOOL_NAME_ALIASES.items() if current == name]


def matches_pattern(match_query: str, matcher: str | None) -> bool:
    """`matchesPattern`."""
    if not matcher or matcher == "*":
        return True
    if _SIMPLE_MATCHER.match(matcher):
        if "|" in matcher:
            return match_query in [normalize_legacy_tool_name(p.strip()) for p in matcher.split("|")]
        return match_query == normalize_legacy_tool_name(matcher)
    try:
        regex = re.compile(matcher)
    except re.error:
        logger.debug("Invalid regex pattern in hook matcher: %s", matcher)
        return False
    if regex.search(match_query):
        return True
    return any(regex.search(legacy) for legacy in get_legacy_tool_names(match_query))


def prepare_if_condition_matcher(
    hook_input: Mapping[str, Any], tools: Sequence[Any] | None
) -> IfConditionMatcher | None:
    """`prepareIfConditionMatcher`: `None` for an event with no tool call to match."""
    # Imported here: `app.loop.tools` imports the tool pipeline, which imports the hooks.
    from app.loop.tools.validation import validate_against_schema

    if str(hook_input.get("hook_event_name")) not in TOOL_EVENTS:
        return None
    raw_name = str(hook_input.get("tool_name", ""))
    tool_name = normalize_legacy_tool_name(raw_name)
    tool = _find_tool(tools, raw_name)
    pattern_matcher: Callable[[str], bool] | None = None
    tool_input = hook_input.get("tool_input")
    if tool is not None and isinstance(tool_input, Mapping):
        schema_ok = validate_against_schema(raw_name, getattr(tool, "input_schema", None), dict(tool_input)) is None
        prepare = getattr(tool, "prepare_permission_matcher", None)
        if schema_ok and callable(prepare):
            pattern_matcher = prepare(dict(tool_input))

    def matcher(if_condition: str) -> bool:
        name, content = permission_rule_value_from_string(if_condition)
        if normalize_legacy_tool_name(name) != tool_name:
            return False
        if not content:
            return True
        return pattern_matcher(content) if pattern_matcher is not None else False

    return matcher


def _find_tool(tools: Sequence[Any] | None, name: str) -> Any:
    for tool in tools or ():
        if getattr(tool, "name", None) == name:
            return tool
    for tool in tools or ():
        if name in getattr(tool, "aliases", ()):
            return tool
    return None


@dataclass(frozen=True, slots=True)
class MatchedHook:
    """`MatchedHook`: a hook and where it came from (`settings` for every hook here)."""

    hook: HookCommand
    hook_source: str = "settings"


def get_hooks_config(
    snapshot: HooksConfigSnapshot,
    hook_event: str,
    session_matchers: Mapping[str, Sequence[HookMatcher]] | None = None,
) -> list[HookMatcher]:
    """
    `getHooksConfig`: the snapshot's matchers, then session-scoped ones (an agent's or a
    skill's frontmatter hooks), which a managed-only policy excludes.
    """
    matchers = list(snapshot.matchers_for(hook_event))
    if not snapshot.managed_only and session_matchers:
        matchers.extend(session_matchers.get(hook_event, ()))
    return matchers


def _dedup_key(hook: HookCommand) -> tuple[str, ...]:
    condition = hook.if_condition or ""
    if isinstance(hook, CommandHook):
        return ("command", hook.shell or DEFAULT_HOOK_SHELL, hook.command, condition)
    if isinstance(hook, HttpHook):
        return ("http", hook.url, condition)
    return (hook.type, hook.prompt, condition)


def _dedup(hooks: list[MatchedHook]) -> list[MatchedHook]:
    """Group by type in upstream's order; within a type the last duplicate wins its slot."""
    ordered: list[MatchedHook] = []
    for kind in ("command", "prompt", "agent", "http"):
        by_key: dict[tuple[str, ...], MatchedHook] = {}
        for matched in hooks:
            if matched.hook.type == kind:
                # A dict, like a JS Map, keeps the first position and takes the last value.
                by_key[_dedup_key(matched.hook)] = matched
        ordered.extend(by_key.values())
    return ordered


def get_matching_hooks(
    snapshot: HooksConfigSnapshot,
    hook_event: str,
    hook_input: Mapping[str, Any],
    tools: Sequence[Any] | None = None,
    session_matchers: Mapping[str, Sequence[HookMatcher]] | None = None,
) -> list[MatchedHook]:
    """`getMatchingHooks`. Never raises: a failure here means no hooks, as upstream."""
    try:
        hook_matchers = get_hooks_config(snapshot, hook_event, session_matchers)
        match_query = match_query_for(hook_input)
        if match_query:
            hook_matchers = [m for m in hook_matchers if not m.matcher or matches_pattern(match_query, m.matcher)]
        matched = [MatchedHook(hook=hook) for m in hook_matchers for hook in m.hooks]
        unique = _dedup(matched)

        if any(m.hook.if_condition for m in unique):
            if_matcher = prepare_if_condition_matcher(hook_input, tools)
        else:
            if_matcher = None
        filtered: list[MatchedHook] = []
        for m in unique:
            condition = m.hook.if_condition
            if not condition:
                filtered.append(m)
                continue
            if if_matcher is None:
                logger.debug(
                    "Hook if condition %r cannot be evaluated for non-tool event %s",
                    condition, hook_input.get("hook_event_name"),
                )
                continue
            if if_matcher(condition):
                filtered.append(m)
        if hook_event in (HookEvent.SESSION_START, HookEvent.SETUP):
            filtered = [m for m in filtered if not isinstance(m.hook, HttpHook)]
        return filtered
    except Exception:
        logger.exception("Failed to collect matching hooks for %s", hook_event)
        return []


def hook_display_text(hook: HookCommand) -> str:
    """`getHookDisplayText`: the status message when set, else the command/prompt/url."""
    if hook.status_message:
        return hook.status_message
    if isinstance(hook, CommandHook):
        return hook.command
    if isinstance(hook, HttpHook):
        return hook.url
    return hook.prompt


__all__ = [
    "IfConditionMatcher",
    "MatchedHook",
    "get_hooks_config",
    "get_legacy_tool_names",
    "get_matching_hooks",
    "hook_display_text",
    "matches_pattern",
    "prepare_if_condition_matcher",
]
