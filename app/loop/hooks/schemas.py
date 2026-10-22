"""
The hook configuration schema: what a `hooks` block in a settings file may contain.

Ported from `reference/claude-code/src/schemas/hooks.ts` -> `HookCommandSchema` (a
discriminated union of `command`, `prompt`, `agent` and `http`), `HookMatcherSchema`,
`HooksSchema`; `SHELL_TYPES` from `src/utils/shell/shellProvider.ts`.

Upstream parses with Zod, which strips unknown keys and rejects a wrong type. That is kept
field for field. *Divergence:* upstream's settings loader rejects a whole settings file
whose hooks fail the schema; here an invalid hook or matcher is logged and dropped on its
own, the convention this repository applies to every configuration file (the permission
rules in the same files are already loaded that way).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, TypeAlias
from urllib.parse import urlparse

from app.loop.hooks.events import is_hook_event

logger = logging.getLogger("TDDOrchestrator.Hooks")

#: `SHELL_TYPES`.
SHELL_TYPES: tuple[str, ...] = ("bash", "powershell")
#: `DEFAULT_HOOK_SHELL`.
DEFAULT_HOOK_SHELL = "bash"


@dataclass(frozen=True, slots=True)
class CommandHook:
    """`BashCommandHookSchema`."""

    command: str
    if_condition: str | None = None
    shell: str | None = None
    timeout: float | None = None
    status_message: str | None = None
    once: bool | None = None
    run_async: bool | None = None
    async_rewake: bool | None = None

    @property
    def type(self) -> str:
        return "command"


@dataclass(frozen=True, slots=True)
class PromptHook:
    """`PromptHookSchema`."""

    prompt: str
    if_condition: str | None = None
    timeout: float | None = None
    model: str | None = None
    status_message: str | None = None
    once: bool | None = None

    @property
    def type(self) -> str:
        return "prompt"


@dataclass(frozen=True, slots=True)
class AgentHook:
    """`AgentHookSchema`."""

    prompt: str
    if_condition: str | None = None
    timeout: float | None = None
    model: str | None = None
    status_message: str | None = None
    once: bool | None = None

    @property
    def type(self) -> str:
        return "agent"


@dataclass(frozen=True, slots=True)
class HttpHook:
    """`HttpHookSchema`."""

    url: str
    if_condition: str | None = None
    timeout: float | None = None
    headers: tuple[tuple[str, str], ...] = ()
    allowed_env_vars: tuple[str, ...] = ()
    status_message: str | None = None
    once: bool | None = None

    @property
    def type(self) -> str:
        return "http"


HookCommand: TypeAlias = CommandHook | PromptHook | AgentHook | HttpHook


@dataclass(frozen=True, slots=True)
class HookMatcher:
    """`HookMatcherSchema`: an optional pattern and the hooks it selects."""

    hooks: tuple[HookCommand, ...]
    matcher: str | None = None


#: `HooksSettings`: event name -> its matchers, in configuration order.
HooksSettings: TypeAlias = Mapping[str, tuple[HookMatcher, ...]]


class HookSchemaError(ValueError):
    """A hook definition that fails the schema."""


def _opt_str(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HookSchemaError(f"'{key}' must be a string")
    return value


def _opt_bool(raw: Mapping[str, Any], key: str) -> bool | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise HookSchemaError(f"'{key}' must be a boolean")
    return value


def _opt_timeout(raw: Mapping[str, Any]) -> float | None:
    value = raw.get("timeout")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise HookSchemaError("'timeout' must be a positive number")
    return float(value)


def _req_str(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise HookSchemaError(f"'{key}' must be a string")
    return value


def _is_url(value: str) -> bool:
    """Zod's `.url()`: a parseable absolute URL with a scheme."""
    parsed = urlparse(value)
    return bool(parsed.scheme) and bool(parsed.netloc or parsed.path)


def parse_hook_command(raw: Any) -> HookCommand:
    """`HookCommandSchema().parse`: one hook definition, or `HookSchemaError`."""
    if not isinstance(raw, Mapping):
        raise HookSchemaError("a hook must be an object")
    kind = raw.get("type")
    if_condition = _opt_str(raw, "if")
    timeout = _opt_timeout(raw)
    status_message = _opt_str(raw, "statusMessage")
    once = _opt_bool(raw, "once")
    if kind == "command":
        shell = _opt_str(raw, "shell")
        if shell is not None and shell not in SHELL_TYPES:
            raise HookSchemaError(f"'shell' must be one of {', '.join(SHELL_TYPES)}")
        return CommandHook(
            command=_req_str(raw, "command"),
            if_condition=if_condition,
            shell=shell,
            timeout=timeout,
            status_message=status_message,
            once=once,
            run_async=_opt_bool(raw, "async"),
            async_rewake=_opt_bool(raw, "asyncRewake"),
        )
    if kind == "prompt":
        return PromptHook(
            prompt=_req_str(raw, "prompt"),
            if_condition=if_condition,
            timeout=timeout,
            model=_opt_str(raw, "model"),
            status_message=status_message,
            once=once,
        )
    if kind == "agent":
        return AgentHook(
            prompt=_req_str(raw, "prompt"),
            if_condition=if_condition,
            timeout=timeout,
            model=_opt_str(raw, "model"),
            status_message=status_message,
            once=once,
        )
    if kind == "http":
        url = _req_str(raw, "url")
        if not _is_url(url):
            raise HookSchemaError("'url' must be a valid URL")
        headers_raw = raw.get("headers")
        headers: tuple[tuple[str, str], ...] = ()
        if headers_raw is not None:
            if not isinstance(headers_raw, Mapping) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in headers_raw.items()
            ):
                raise HookSchemaError("'headers' must map strings to strings")
            headers = tuple((str(k), str(v)) for k, v in headers_raw.items())
        env_raw = raw.get("allowedEnvVars")
        allowed: tuple[str, ...] = ()
        if env_raw is not None:
            if not isinstance(env_raw, list) or not all(isinstance(v, str) for v in env_raw):
                raise HookSchemaError("'allowedEnvVars' must be a list of strings")
            allowed = tuple(env_raw)
        return HttpHook(
            url=url,
            if_condition=if_condition,
            timeout=timeout,
            headers=headers,
            allowed_env_vars=allowed,
            status_message=status_message,
            once=once,
        )
    raise HookSchemaError(f"unknown hook type: {kind!r}")


def parse_hook_matcher(raw: Any, where: str = "") -> HookMatcher | None:
    """One matcher with its valid hooks; `None` when the matcher itself is malformed."""
    if not isinstance(raw, Mapping):
        logger.warning("Ignoring malformed hook matcher%s: not an object", where)
        return None
    matcher = raw.get("matcher")
    if matcher is not None and not isinstance(matcher, str):
        logger.warning("Ignoring hook matcher%s: 'matcher' must be a string", where)
        return None
    hooks_raw = raw.get("hooks")
    if not isinstance(hooks_raw, list):
        logger.warning("Ignoring hook matcher%s: 'hooks' must be a list", where)
        return None
    hooks: list[HookCommand] = []
    for entry in hooks_raw:
        try:
            hooks.append(parse_hook_command(entry))
        except HookSchemaError as exc:
            logger.warning("Ignoring invalid hook%s: %s", where, exc)
    return HookMatcher(hooks=tuple(hooks), matcher=matcher)


#: Every field a hook definition may carry; Zod's `.optional()` rejects an explicit `null`.
_HOOK_FIELDS = frozenset(
    {"type", "command", "if", "shell", "timeout", "statusMessage", "once", "async", "asyncRewake",
     "prompt", "model", "url", "headers", "allowedEnvVars"}
)


def hooks_schema_errors(raw: Any) -> list[str]:
    """
    `HooksSchema().safeParse` issues, each as `path: message`; empty when valid. Unknown keys
    inside a hook are stripped (not an error), as Zod strips them; an unknown event, a wrong
    type or an explicit `null` is an error.
    """
    if not isinstance(raw, Mapping):
        return ["hooks: Expected object"]
    errors: list[str] = []
    for event, matchers in raw.items():
        if not isinstance(event, str) or not is_hook_event(event):
            errors.append(f"hooks.{event}: Invalid key in record")
            continue
        if not isinstance(matchers, list):
            errors.append(f"hooks.{event}: Expected array")
            continue
        for i, matcher in enumerate(matchers):
            where = f"hooks.{event}.{i}"
            if not isinstance(matcher, Mapping):
                errors.append(f"{where}: Expected object")
                continue
            if "matcher" in matcher and not isinstance(matcher["matcher"], str):
                errors.append(f"{where}.matcher: Expected string")
            hooks = matcher.get("hooks")
            if not isinstance(hooks, list):
                errors.append(f"{where}.hooks: Expected array")
                continue
            for j, hook in enumerate(hooks):
                hook_where = f"{where}.hooks.{j}"
                if isinstance(hook, Mapping):
                    nulls = sorted(k for k in hook if k in _HOOK_FIELDS and hook[k] is None)
                    errors.extend(f"{hook_where}.{k}: Expected value, received null" for k in nulls)
                    if nulls:
                        continue
                try:
                    parse_hook_command(hook)
                except HookSchemaError as exc:
                    errors.append(f"{hook_where}: {exc}")
    return errors


def parse_hooks_settings(raw: Any, origin: str = "") -> dict[str, tuple[HookMatcher, ...]]:
    """`HooksSchema().parse`, leniently: unknown events and invalid entries are dropped."""
    if raw is None:
        return {}
    where = f" in {origin}" if origin else ""
    if not isinstance(raw, Mapping):
        logger.warning("Ignoring 'hooks'%s: not an object", where)
        return {}
    parsed: dict[str, tuple[HookMatcher, ...]] = {}
    for event, matchers_raw in raw.items():
        if not isinstance(event, str) or not is_hook_event(event):
            logger.warning("Ignoring hooks for unknown event %r%s", event, where)
            continue
        if not isinstance(matchers_raw, list):
            logger.warning("Ignoring hooks for %s%s: not a list", event, where)
            continue
        matchers = [m for m in (parse_hook_matcher(r, where) for r in matchers_raw) if m is not None]
        parsed[event] = tuple(matchers)
    return parsed


__all__ = [
    "DEFAULT_HOOK_SHELL",
    "SHELL_TYPES",
    "AgentHook",
    "CommandHook",
    "HookCommand",
    "HookMatcher",
    "HookSchemaError",
    "HooksSettings",
    "hooks_schema_errors",
    "HttpHook",
    "PromptHook",
    "parse_hook_command",
    "parse_hook_matcher",
    "parse_hooks_settings",
]
