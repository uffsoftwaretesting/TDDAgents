"""
Hook settings: discovery across three scopes, and the merge that produces one ordered
list of hooks per event.

    ~/.tddagents/settings.json          user      — personal, every project
    <repo>/.tddagents/settings.json     project   — shared, version-controlled
    <repo>/.tddagents/settings.local.json  local  — personal, this project, gitignored

Later scopes append to earlier ones rather than replacing them, so a project's quality
gate cannot be silently switched off by a personal file — a local hook can add a veto, not
remove one.

Every layer is optional and every layer is parsed defensively: a malformed file is logged
and skipped, never fatal.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Sequence

from app.hooks.events import HOOK_EVENTS, HookEvent
from app.hooks.schemas import (
    DEFAULT_HOOK_TIMEOUT,
    DEFAULT_PROMPT_TIMEOUT,
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

logger = logging.getLogger("TDDOrchestrator.Hooks")

SETTINGS_DIR = ".tddagents"
SETTINGS_FILENAME = "settings.json"
LOCAL_SETTINGS_FILENAME = "settings.local.json"

#: Supported hook events in TDDAgents (Part H1).
KNOWN_EVENTS: tuple[str, ...] = HOOK_EVENTS
TOOL_HOOK_EVENTS: tuple[str, ...] = (HookEvent.PRE_TOOL_USE.value, HookEvent.POST_TOOL_USE.value)


def _load_one(
    path: Path,
    supported_events: Sequence[str] = HOOK_EVENTS,
) -> dict[str, list[HookMatcher]]:
    """Reads and parses one settings file. Returns empty on anything unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring malformed hook settings at %s: %s", path, exc)
        return {}

    if not isinstance(document, dict):
        logger.warning("Ignoring hook settings at %s: the top level is not an object.", path)
        return {}

    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return {}

    parsed: dict[str, list[HookMatcher]] = {}
    for event, raw in hooks.items():
        if event not in supported_events:
            logger.warning("Ignoring unsupported hook event '%s' in %s.", event, path)
            continue
        if not isinstance(raw, list):
            logger.warning("Ignoring a non-list hook event in %s.", path)
            continue
        matchers: list[HookMatcher] = []
        for entry in raw:
            m = parse_matcher(entry, str(path))
            if m is not None:
                matchers.append(m)
        if matchers:
            parsed[event] = matchers
    return parsed


def settings_paths(project_root: Path, home: Path | None = None) -> list[Path]:
    """The three candidate files, in merge order. Existence is not checked here."""
    base = home if home is not None else Path.home()
    return [
        base / SETTINGS_DIR / SETTINGS_FILENAME,
        project_root / SETTINGS_DIR / SETTINGS_FILENAME,
        project_root / SETTINGS_DIR / LOCAL_SETTINGS_FILENAME,
    ]


def load_hook_settings(
    project_root: Path | str,
    home: Path | None = None,
    supported_events: Sequence[str] = HOOK_EVENTS,
) -> HookSettings:
    """
    Loads and merges all three scopes.

    Args:
        project_root: The repository root; the project and local scopes hang off it.
        home: Overrides the user scope's base directory.
        supported_events: Allowed hook event names to load.
    """
    merged: dict[str, list[HookMatcher]] = {event: [] for event in supported_events}
    sources: list[str] = []

    for path in settings_paths(Path(project_root), home):
        if not path.is_file():
            continue
        parsed = _load_one(path, supported_events)
        if not parsed:
            continue
        sources.append(str(path))
        for event, matchers in parsed.items():
            if event in merged:
                merged[event].extend(matchers)
            else:
                merged[event] = list(matchers)

    return HookSettings(
        events={event: tuple(matchers) for event, matchers in merged.items() if matchers},
        sources=tuple(sources),
    )


__all__ = [
    "SETTINGS_DIR",
    "SETTINGS_FILENAME",
    "LOCAL_SETTINGS_FILENAME",
    "KNOWN_EVENTS",
    "TOOL_HOOK_EVENTS",
    "DEFAULT_HOOK_TIMEOUT",
    "DEFAULT_PROMPT_TIMEOUT",
    "HookType",
    "HookDefinition",
    "CommandHook",
    "HookCommand",
    "PromptHook",
    "AgentHook",
    "HttpHook",
    "HookMatcher",
    "HookSettings",
    "parse_hook",
    "parse_matcher",
    "settings_paths",
    "load_hook_settings",
]
