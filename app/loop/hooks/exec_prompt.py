"""
Running one prompt hook: a single model call that answers `{"ok": bool, "reason"?: str}`.

Ported from:
- `reference/claude-code/src/utils/hooks/execPromptHook.ts` -> `execPromptHook`
- `reference/claude-code/src/utils/hooks/hookHelpers.ts` -> `hookResponseSchema`,
  `addArgumentsToPrompt`
- `reference/claude-code/src/utils/argumentSubstitution.ts` -> `substituteArguments`,
  `parseArguments`
- `reference/claude-code/src/utils/model/model.ts` -> `getSmallFastModel`

Outcomes, as upstream:

* the reply is not JSON -> `non_blocking_error` ("JSON validation failed");
* the reply fails the `{ok, reason?}` schema -> `non_blocking_error` ("Schema validation
  failed: ...");
* `ok: false` -> `blocking`, **and** `prevent_continuation` with the reason as `stop_reason`
  (a prompt hook that says no stops the run; an agent hook only blocks);
* `ok: true` -> `success` with an empty `hook_success` attachment;
* aborted or timed out (the hook's `timeout` seconds, else 30 s) -> `cancelled`;
* any other error -> `non_blocking_error` ("Error executing prompt hook: ...").

The model is called through `PromptModelCaller`, so tests drive it with a fake.

*Divergences:* upstream sends the session's tools with the request (they share the prompt
cache with the main loop; the model is not expected to call them, and nothing runs them). This
loop has no shared request cache, so the request carries none. Thinking is not configurable
through the LangChain seam, so upstream's `thinkingConfig: disabled` has no equivalent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from app.loop.hooks.messages import HookAttachment, HookBlockingError
from app.loop.hooks.output import HookResult
from app.loop.hooks.process import ABORT_POLL_SECONDS, is_aborted
from app.loop.hooks.schemas import PromptHook
from app.loop.messages import Message

logger = logging.getLogger("TDDOrchestrator.Hooks")

#: `execPromptHook`: `hook.timeout ? hook.timeout * 1000 : 30000`.
DEFAULT_PROMPT_HOOK_TIMEOUT_MS = 30000

#: `getSmallFastModel`'s override variable, renamed (`ANTHROPIC_SMALL_FAST_MODEL`).
SMALL_FAST_MODEL_ENV = "TDDAGENTS_SMALL_FAST_MODEL"

PROMPT_HOOK_SYSTEM_PROMPT = """You are evaluating a hook in TDDAgents.

Your response must be a JSON object matching one of the following schemas:
1. If the condition is met, return: {"ok": true}
2. If the condition is not met, return: {"ok": false, "reason": "Reason for why it is not met"}"""

#: The `outputFormat` JSON schema upstream sends with the request.
HOOK_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class PromptModelCaller(Protocol):
    """One non-streaming model call; returns the reply's text content."""

    def __call__(
        self, messages: Sequence[Message], system_prompt: str, model: str, output_schema: Mapping[str, Any]
    ) -> Awaitable[str]: ...


class HookCancelled(Exception):
    """The hook's combined signal (parent abort or timeout) fired."""


def get_small_fast_model(default: str | None = None) -> str:
    """
    `getSmallFastModel` (`src/utils/model/model.ts`): upstream reads
    `ANTHROPIC_SMALL_FAST_MODEL`, else the default Haiku model. Here the variable is renamed
    `TDDAGENTS_SMALL_FAST_MODEL`, and the fallback is `default` — the dispatcher passes the
    run's model (`RunConfig.model`), since this build has no small-model tier. `Config.MODEL`
    is reached only when no default is given.
    """
    override = os.environ.get(SMALL_FAST_MODEL_ENV)
    if override:
        return override
    if default is not None:
        return default
    from app.config.config import Config

    return str(Config.MODEL)


def parse_arguments(args: str) -> list[str]:
    """`parseArguments`: shell-style split, falling back to whitespace on a parse error."""
    if not args or not args.strip():
        return []
    try:
        return shlex.split(args)
    except ValueError:
        return [a for a in re.split(r"\s+", args) if a]


def substitute_arguments(content: str, args: str | None, append_if_no_placeholder: bool = True) -> str:
    """
    `substituteArguments` without named arguments (hooks have none): `$ARGUMENTS[n]`, `$n`,
    then `$ARGUMENTS`; with no placeholder at all, `ARGUMENTS: <args>` is appended.
    """
    if args is None:
        return content
    parsed = parse_arguments(args)
    original = content

    def indexed(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return parsed[index] if index < len(parsed) else ""

    content = re.sub(r"\$ARGUMENTS\[(\d+)\]", indexed, content)
    content = re.sub(r"\$(\d+)(?!\w)", indexed, content)
    content = content.replace("$ARGUMENTS", args)
    if content == original and append_if_no_placeholder and args:
        content = f"{content}\n\nARGUMENTS: {args}"
    return content


def add_arguments_to_prompt(prompt: str, json_input: str) -> str:
    """`addArgumentsToPrompt`."""
    return substitute_arguments(prompt, json_input)


def parse_hook_response(data: Any) -> tuple[dict[str, Any] | None, str | None]:
    """`hookResponseSchema().safeParse`: `(value, None)` or `(None, error message)`."""
    if not isinstance(data, Mapping):
        return None, "Expected object, received " + type(data).__name__
    if not isinstance(data.get("ok"), bool):
        return None, "ok: Expected boolean"
    reason = data.get("reason")
    if reason is not None and not isinstance(reason, str):
        return None, "reason: Expected string"
    value: dict[str, Any] = {"ok": data["ok"]}
    if reason is not None:
        value["reason"] = reason
    return value, None


async def run_with_signal(
    operation: Callable[[], Awaitable[Any]], signal: Any, timeout_ms: int
) -> Any:
    """
    `createCombinedAbortSignal(signal, {timeoutMs})` for a coroutine: run it, and raise
    `HookCancelled` (cancelling it) when the parent signal fires or the timeout passes.
    """
    if is_aborted(signal):
        raise HookCancelled()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    task = asyncio.ensure_future(operation())
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0 or is_aborted(signal):
                raise HookCancelled()
            done, _ = await asyncio.wait({task}, timeout=min(ABORT_POLL_SECONDS, remaining))
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()


def _js_falsy(value: Any) -> bool:
    """`!json` after `safeParseJSON`: a parse failure, or a falsy JSON scalar."""
    if value is None or value is False or value == "":
        return True
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def _non_blocking(
    hook: PromptHook, hook_name: str, tool_use_id: str, hook_event: str, stderr: str, stdout: str
) -> HookResult:
    return HookResult(
        hook=hook,
        outcome="non_blocking_error",
        attachment=HookAttachment(
            type="hook_non_blocking_error",
            hook_name=hook_name,
            tool_use_id=tool_use_id,
            hook_event=hook_event,
            stderr=stderr,
            stdout=stdout,
            exit_code=1,
        ),
    )


async def exec_prompt_hook(
    hook: PromptHook,
    hook_name: str,
    hook_event: str,
    json_input: str,
    signal: Any,
    tool_context: Any,
    messages: Sequence[Message] | None,
    tool_use_id: str,
    *,
    model_caller: PromptModelCaller,
    default_model: str | None = None,
) -> HookResult:
    """`execPromptHook`."""
    effective_id = tool_use_id or f"hook-{uuid.uuid4()}"
    try:
        processed = add_arguments_to_prompt(hook.prompt, json_input)
        user_message = HumanMessage(content=processed)
        to_query: list[Message] = [*messages, user_message] if messages else [user_message]
        timeout_ms = int(hook.timeout * 1000) if hook.timeout else DEFAULT_PROMPT_HOOK_TIMEOUT_MS
        model = hook.model or get_small_fast_model(default_model)
        try:
            content = await run_with_signal(
                lambda: model_caller(to_query, PROMPT_HOOK_SYSTEM_PROMPT, model, HOOK_RESPONSE_JSON_SCHEMA),
                signal,
                timeout_ms,
            )
        except HookCancelled:
            return HookResult(hook=hook, outcome="cancelled")

        full = str(content).strip()
        try:
            data = json.loads(full)
        except ValueError:
            data = None
        if _js_falsy(data):
            logger.debug("Hooks: error parsing response as JSON: %s", full)
            return _non_blocking(hook, hook_name, effective_id, hook_event, "JSON validation failed", full)

        parsed, error = parse_hook_response(data)
        if parsed is None:
            return _non_blocking(
                hook, hook_name, effective_id, hook_event, f"Schema validation failed: {error}", full
            )

        if not parsed["ok"]:
            reason = parsed.get("reason")
            return HookResult(
                hook=hook,
                outcome="blocking",
                blocking_error=HookBlockingError(
                    blocking_error=f"Prompt hook condition was not met: {reason}", command=hook.prompt
                ),
                prevent_continuation=True,
                stop_reason=reason,
            )

        return HookResult(
            hook=hook,
            outcome="success",
            attachment=HookAttachment(
                type="hook_success",
                hook_name=hook_name,
                tool_use_id=effective_id,
                hook_event=hook_event,
                content="",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - upstream reports every error as non-blocking
        return _non_blocking(hook, hook_name, effective_id, hook_event, f"Error executing prompt hook: {exc}", "")


def _text_of(content: Any) -> str:
    """`extractTextContent`: a string, or the text blocks of a block list joined."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or ():
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, Mapping) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def build_prompt_model_caller(provider: str | None = None) -> PromptModelCaller:
    """
    The production caller: `queryModelWithoutStreaming` on this repo's model seam.

    **Exempt from unit/mutation testing like the seam it wraps**
    (`app/utils/chat_model_factory.py`): every line below talks to a live provider.
    """

    async def call(
        messages: Sequence[Message], system_prompt: str, model: str, output_schema: Mapping[str, Any]
    ) -> str:  # pragma: no cover - live provider
        from app.config.config import Config
        from app.utils.chat_model_factory import get_chat_model

        chat = get_chat_model(provider or Config.CHAT_MODEL, model=model)
        reply = await chat.ainvoke([SystemMessage(content=system_prompt), *messages])
        return _text_of(getattr(reply, "content", ""))

    return call


__all__ = [
    "DEFAULT_PROMPT_HOOK_TIMEOUT_MS",
    "HOOK_RESPONSE_JSON_SCHEMA",
    "PROMPT_HOOK_SYSTEM_PROMPT",
    "SMALL_FAST_MODEL_ENV",
    "HookCancelled",
    "PromptModelCaller",
    "add_arguments_to_prompt",
    "build_prompt_model_caller",
    "exec_prompt_hook",
    "get_small_fast_model",
    "parse_arguments",
    "parse_hook_response",
    "run_with_signal",
    "substitute_arguments",
]
