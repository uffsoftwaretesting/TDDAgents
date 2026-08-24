"""
Hook settings: discovery across three scopes, and the merge that produces one ordered
list of hooks per event.

    ~/.tddagents/settings.json          user      — personal, every project
    <repo>/.tddagents/settings.json     project   — shared, version-controlled
    <repo>/.tddagents/settings.local.json  local  — personal, this project, gitignored

Later scopes append to earlier ones rather than replacing them, so a project's quality
gate cannot be silently switched off by a personal file — a local hook can add a veto, not
remove one. That is the opposite of how a "precedence" that overwrote would behave, and it
is the safer direction for a config whose whole job is to say no.

Every layer is optional and every layer is parsed defensively: a malformed file is logged
and skipped, never fatal. A typo in one settings file must not take hook dispatch down for
the other two, for the same reason a broken agent definition must not empty the registry.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("TDDOrchestrator.Hooks")

SETTINGS_DIR = ".tddagents"
SETTINGS_FILENAME = "settings.json"
LOCAL_SETTINGS_FILENAME = "settings.local.json"

#: The only hook events Phase 1B dispatches. The vocabulary elsewhere is much larger;
#: TDDAgents needs exactly the two that wrap a tool call.
KNOWN_EVENTS = ("PreToolUse", "PostToolUse")

DEFAULT_HOOK_TIMEOUT = 60.0


@dataclass(frozen=True)
class HookCommand:
    """One shell command to run for a matching event."""

    command: str
    timeout: float = DEFAULT_HOOK_TIMEOUT
    if_condition: str | None = None
    status_message: str = ""
    source: str = ""

    @property
    def label(self) -> str:
        return self.status_message or self.command


@dataclass(frozen=True)
class HookMatcher:
    """A tool-name pattern and the commands it triggers."""

    matcher: str | None
    hooks: tuple[HookCommand, ...]


@dataclass(frozen=True)
class HookSettings:
    """The merged configuration, keyed by event name."""

    events: dict[str, tuple[HookMatcher, ...]] = field(default_factory=dict)
    sources: tuple[str, ...] = ()

    def matchers_for(self, event: str) -> tuple[HookMatcher, ...]:
        return self.events.get(event, ())

    @property
    def is_empty(self) -> bool:
        return not any(self.events.values())

    def describe(self) -> str:
        """
        A one-line summary recorded at run start.

        Hooks are host-side processes, so without knowing which ones were active the event
        log cannot explain why a run behaved as it did.
        """
        if not self.sources:
            return "no hook settings found"
        counts = ", ".join(f"{event}={len(self.events.get(event, ()))}" for event in KNOWN_EVENTS)
        return f"{counts} from {', '.join(self.sources)}"


def _parse_command(raw: object, source: str) -> HookCommand | None:
    if not isinstance(raw, dict):
        logger.warning("Ignoring a non-object hook entry in %s.", source)
        return None

    if raw.get("type", "command") != "command":
        logger.warning(
            "Ignoring hook of type '%s' in %s: only 'command' hooks are supported.",
            raw.get("type"),
            source,
        )
        return None

    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        logger.warning("Ignoring a hook with no command in %s.", source)
        return None

    timeout = raw.get("timeout", DEFAULT_HOOK_TIMEOUT)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        logger.warning("Hook '%s' in %s has an invalid timeout; using the default.", command, source)
        timeout = DEFAULT_HOOK_TIMEOUT

    condition = raw.get("if")
    status = raw.get("statusMessage")

    return HookCommand(
        command=command,
        timeout=float(timeout),
        if_condition=condition if isinstance(condition, str) and condition else None,
        status_message=status if isinstance(status, str) else "",
        source=source,
    )


def _parse_matchers(raw: object, source: str) -> list[HookMatcher]:
    if not isinstance(raw, list):
        logger.warning("Ignoring a non-list hook event in %s.", source)
        return []

    matchers: list[HookMatcher] = []
    for entry in raw:
        if not isinstance(entry, dict):
            logger.warning("Ignoring a non-object matcher in %s.", source)
            continue

        matcher = entry.get("matcher")
        commands = [
            parsed
            for parsed in (_parse_command(item, source) for item in entry.get("hooks", []) or [])
            if parsed is not None
        ]
        if not commands:
            continue

        matchers.append(
            HookMatcher(
                matcher=matcher if isinstance(matcher, str) and matcher else None,
                hooks=tuple(commands),
            )
        )
    return matchers


def _load_one(path: Path) -> dict[str, list[HookMatcher]]:
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
        if event not in KNOWN_EVENTS:
            logger.warning("Ignoring unsupported hook event '%s' in %s.", event, path)
            continue
        matchers = _parse_matchers(raw, str(path))
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


def load_hook_settings(project_root: Path | str, home: Path | None = None) -> HookSettings:
    """
    Loads and merges all three scopes.

    Args:
        project_root: The repository root; the project and local scopes hang off it.
        home: Overrides the user scope's base directory. Tests pass a tmp_path here so a
              developer's real `~/.tddagents` can never influence a test run.
    """
    merged: dict[str, list[HookMatcher]] = {event: [] for event in KNOWN_EVENTS}
    sources: list[str] = []

    for path in settings_paths(Path(project_root), home):
        if not path.is_file():
            continue
        parsed = _load_one(path)
        if not parsed:
            continue
        sources.append(str(path))
        for event, matchers in parsed.items():
            merged[event].extend(matchers)

    return HookSettings(
        events={event: tuple(matchers) for event, matchers in merged.items()},
        sources=tuple(sources),
    )
