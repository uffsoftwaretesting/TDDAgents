"""
Hook schemas and lossless configuration serialization (Part H2).

Ported from:
- `reference/claude-code/src/schemas/hooks.ts` -> `HookCommandSchema`, `HookMatcherSchema`, `HooksSchema`
- `reference/claude-code/src/types/hooks.ts`

Note on H2 invariant:
A schema used for round-tripping user configuration must NOT contain transforms
producing non-serializable values (such as closures or function objects), or
saving the file silently deletes the user's own settings.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.hooks.events import HOOK_EVENTS

logger = logging.getLogger("TDDOrchestrator.Hooks")

DEFAULT_HOOK_TIMEOUT = 60.0
DEFAULT_PROMPT_TIMEOUT = 30.0


class HookType(StrEnum):
    COMMAND = "command"
    PROMPT = "prompt"
    AGENT = "agent"
    HTTP = "http"


@dataclass(frozen=True)
class HookDefinition:
    """Base class for all hook specifications."""

    type: str
    timeout: float = DEFAULT_HOOK_TIMEOUT
    if_condition: str | None = None
    status_message: str = ""
    source: str = ""
    once: bool = False

    @property
    def label(self) -> str:
        return self.status_message or getattr(self, "command", getattr(self, "prompt", getattr(self, "url", self.type)))

    def to_dict(self) -> dict[str, Any]:
        """Convert hook to a JSON-serializable dictionary without loss."""
        data: dict[str, Any] = {"type": self.type}
        if self.timeout != DEFAULT_HOOK_TIMEOUT:
            data["timeout"] = self.timeout
        if self.if_condition is not None:
            data["if"] = self.if_condition
        if self.status_message:
            data["statusMessage"] = self.status_message
        if self.once:
            data["once"] = self.once
        return data


@dataclass(frozen=True)
class CommandHook(HookDefinition):
    """Shell command hook executed on the host."""

    type: str = HookType.COMMAND.value
    command: str = ""
    shell: str = "bash"
    async_: bool = False
    async_rewake: bool = False

    @property
    def label(self) -> str:
        return self.status_message or self.command

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["command"] = self.command
        if self.shell != "bash":
            data["shell"] = self.shell
        if self.async_:
            data["async"] = self.async_
        if self.async_rewake:
            data["asyncRewake"] = self.async_rewake
        return data


# Backward-compatibility alias
HookCommand = CommandHook


@dataclass(frozen=True)
class PromptHook(HookDefinition):
    """LLM prompt hook evaluated against hook arguments."""

    type: str = HookType.PROMPT.value
    prompt: str = ""
    model: str | None = None
    timeout: float = DEFAULT_PROMPT_TIMEOUT

    @property
    def label(self) -> str:
        return self.status_message or self.prompt

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["prompt"] = self.prompt
        if self.model is not None:
            data["model"] = self.model
        return data


@dataclass(frozen=True)
class AgentHook(HookDefinition):
    """Agentic verifier hook evaluating the condition with tools."""

    type: str = HookType.AGENT.value
    prompt: str = ""
    model: str | None = None
    timeout: float = DEFAULT_HOOK_TIMEOUT

    @property
    def label(self) -> str:
        return self.status_message or self.prompt

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["prompt"] = self.prompt
        if self.model is not None:
            data["model"] = self.model
        return data


@dataclass(frozen=True)
class HttpHook(HookDefinition):
    """HTTP POST hook sending JSON input to a URL endpoint."""

    type: str = HookType.HTTP.value
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    allowed_env_vars: tuple[str, ...] = ()
    timeout: float = DEFAULT_HOOK_TIMEOUT

    @property
    def label(self) -> str:
        return self.status_message or self.url

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["url"] = self.url
        if self.headers:
            data["headers"] = dict(self.headers)
        if self.allowed_env_vars:
            data["allowedEnvVars"] = list(self.allowed_env_vars)
        return data


@dataclass(frozen=True)
class HookMatcher:
    """A tool/event match pattern paired with hooks to run on match."""

    matcher: str | None
    hooks: tuple[HookDefinition, ...]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "hooks": [h.to_dict() for h in self.hooks],
        }
        if self.matcher is not None:
            data["matcher"] = self.matcher
        return data


@dataclass(frozen=True)
class HookSettings:
    """The merged hook configuration across user, project, and local scopes."""

    events: dict[str, tuple[HookMatcher, ...]] = field(default_factory=dict)
    sources: tuple[str, ...] = ()

    def matchers_for(self, event: str) -> tuple[HookMatcher, ...]:
        return self.events.get(event, ())

    @property
    def is_empty(self) -> bool:
        return not any(self.events.values())

    def describe(self) -> str:
        """A one-line summary of active hooks recorded in the run log."""
        if not self.sources:
            return "no hook settings found"
        # Primary tool events for concise summary
        primary = ("PreToolUse", "PostToolUse")
        counts = ", ".join(f"{evt}={len(self.events.get(evt, ()))}" for evt in primary)
        return f"{counts} from {', '.join(self.sources)}"

    def to_dict(self) -> dict[str, Any]:
        """Losslessly serialize settings to a plain dict."""
        hooks_dict: dict[str, list[dict[str, Any]]] = {}
        for event, matchers in self.events.items():
            if matchers:
                hooks_dict[event] = [m.to_dict() for m in matchers]
        return {"hooks": hooks_dict}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: str = "") -> HookSettings:
        if not isinstance(data, dict):
            return cls()
        hooks_raw = data.get("hooks")
        if not isinstance(hooks_raw, dict):
            return cls()

        events_parsed: dict[str, tuple[HookMatcher, ...]] = {}
        for event_name, matchers_raw in hooks_raw.items():
            if event_name not in HOOK_EVENTS:
                logger.warning("Ignoring unsupported hook event '%s' in %s.", event_name, source)
                continue
            if not isinstance(matchers_raw, list):
                logger.warning("Ignoring a non-list hook event in %s.", source)
                continue
            matchers: list[HookMatcher] = []
            for item in matchers_raw:
                parsed_m = parse_matcher(item, source)
                if parsed_m is not None:
                    matchers.append(parsed_m)
            if matchers:
                events_parsed[event_name] = tuple(matchers)

        sources = (source,) if source else ()
        return cls(events=events_parsed, sources=sources)

    @classmethod
    def from_json(cls, text: str, source: str = "") -> HookSettings:
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("Ignoring malformed hook settings in %s: %s", source, exc)
            return cls()
        return cls.from_dict(doc, source)


def _parse_timeout(raw: Any, default: float) -> float:
    timeout = raw
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        return default
    return float(timeout)


def parse_hook(raw: object, source: str = "") -> HookDefinition | None:
    """Defensively parses one hook object into the appropriate HookDefinition."""
    if not isinstance(raw, dict):
        logger.warning("Ignoring a non-object hook entry in %s.", source)
        return None

    hook_type = raw.get("type", HookType.COMMAND.value)
    if not isinstance(hook_type, str):
        return None

    cond = raw.get("if")
    if_condition = cond if isinstance(cond, str) and cond.strip() else None

    status = raw.get("statusMessage")
    status_message = status if isinstance(status, str) else ""

    once = bool(raw.get("once", False))

    if hook_type == HookType.COMMAND.value:
        cmd = raw.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            logger.warning("Ignoring a command hook with no command in %s.", source)
            return None
        timeout = _parse_timeout(raw.get("timeout"), DEFAULT_HOOK_TIMEOUT)
        shell = raw.get("shell", "bash")
        return CommandHook(
            command=cmd,
            shell=shell if isinstance(shell, str) else "bash",
            timeout=timeout,
            if_condition=if_condition,
            status_message=status_message,
            source=source,
            once=once,
            async_=bool(raw.get("async", False)),
            async_rewake=bool(raw.get("asyncRewake", False)),
        )

    if hook_type == HookType.PROMPT.value:
        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            logger.warning("Ignoring a prompt hook with no prompt in %s.", source)
            return None
        timeout = _parse_timeout(raw.get("timeout"), DEFAULT_PROMPT_TIMEOUT)
        model = raw.get("model")
        return PromptHook(
            prompt=prompt,
            model=model if isinstance(model, str) else None,
            timeout=timeout,
            if_condition=if_condition,
            status_message=status_message,
            source=source,
            once=once,
        )

    if hook_type == HookType.AGENT.value:
        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            logger.warning("Ignoring an agent hook with no prompt in %s.", source)
            return None
        timeout = _parse_timeout(raw.get("timeout"), DEFAULT_HOOK_TIMEOUT)
        model = raw.get("model")
        return AgentHook(
            prompt=prompt,
            model=model if isinstance(model, str) else None,
            timeout=timeout,
            if_condition=if_condition,
            status_message=status_message,
            source=source,
            once=once,
        )

    if hook_type == HookType.HTTP.value:
        url = raw.get("url")
        if not isinstance(url, str) or not url.strip():
            logger.warning("Ignoring an http hook with no url in %s.", source)
            return None
        timeout = _parse_timeout(raw.get("timeout"), DEFAULT_HOOK_TIMEOUT)
        headers = raw.get("headers")
        headers_dict = {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {}
        allowed_env = raw.get("allowedEnvVars")
        allowed_tuple = (
            tuple(str(x) for x in allowed_env if isinstance(x, str))
            if isinstance(allowed_env, list)
            else ()
        )
        return HttpHook(
            url=url,
            headers=headers_dict,
            allowed_env_vars=allowed_tuple,
            timeout=timeout,
            if_condition=if_condition,
            status_message=status_message,
            source=source,
            once=once,
        )

    logger.warning("Ignoring unsupported hook type '%s' in %s.", hook_type, source)
    return None


def parse_matcher(raw: object, source: str = "") -> HookMatcher | None:
    """Defensively parses one matcher entry."""
    if not isinstance(raw, dict):
        logger.warning("Ignoring a non-object matcher in %s.", source)
        return None

    matcher = raw.get("matcher")
    matcher_str = matcher if isinstance(matcher, str) and matcher.strip() else None

    hooks_raw = raw.get("hooks", [])
    if not isinstance(hooks_raw, list):
        return None

    parsed_hooks: list[HookDefinition] = []
    for item in hooks_raw:
        h = parse_hook(item, source)
        if h is not None:
            parsed_hooks.append(h)

    if not parsed_hooks:
        return None

    return HookMatcher(matcher=matcher_str, hooks=tuple(parsed_hooks))
