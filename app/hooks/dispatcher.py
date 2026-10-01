"""
The hook dispatcher: tool middleware and lifecycle hook execution (Parts H1, H3, H4).

Ported from:
- `reference/claude-code/src/utils/hooks.ts` -> `getMatchingHooks`, `executeHooksOutsideREPL`
- `reference/claude-code/src/services/tools/toolHooks.ts` -> `runPreToolUseHooks`, `runPostToolUseHooks`

Exit-code contract (H3):
    exit 0      proceed
    exit 2      PreToolUse  -> veto the call; stderr/reason becomes the reason the model reads
                PostToolUse -> tool already ran; output stands; step marked stop_continuation
                Stop        -> blocks turn exit; model retries with blocking error message
    otherwise   logged and ignored — fails open for transient errors
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.hooks.backends import (
    BLOCKING_EXIT_CODE,
    AgentHookBackend,
    AgentRunner,
    CommandHookBackend,
    HttpClient,
    HttpHookBackend,
    ModelCaller,
    PromptHookBackend,
)
from app.hooks.config import HookSettings, load_hook_settings
from app.hooks.events import HookEvent, extract_match_key, matches_pattern
from app.hooks.matching import deduplicate_hooks, eval_if_condition
from app.hooks.schemas import AgentHook, CommandHook, HookDefinition, HttpHook, PromptHook

logger = logging.getLogger("TDDOrchestrator.Hooks")


@dataclass
class HookOutcome:
    """The merged verdict of every hook that ran for one event."""

    denied: bool = False
    reason: str = ""
    updated_input: dict[str, Any] | None = None
    additional_context: str = ""
    stop_continuation: bool = False
    prevent_continuation: bool = False
    blocking_errors: tuple[str, ...] = ()


def _matches(matcher: str | None, tool_name: str) -> bool:
    """Backward-compatible matcher function."""
    return matches_pattern(tool_name, matcher)


class HookDispatcher:
    """Dispatches lifecycle and tool hooks across configured backends."""

    def __init__(
        self,
        settings: HookSettings,
        project_root: Path | str = ".",
        *,
        model_caller: ModelCaller | None = None,
        agent_runner: AgentRunner | None = None,
        http_client: HttpClient | None = None,
    ) -> None:
        self.settings = settings
        self.project_root = Path(project_root)
        self.command_backend = CommandHookBackend(self.project_root)
        self.prompt_backend = PromptHookBackend(model_caller)
        self.agent_backend = AgentHookBackend(agent_runner)
        self.http_backend = HttpHookBackend(http_client)
        logger.info("🪝 Hook configuration: %s", settings.describe())

    @classmethod
    def from_project(
        cls,
        project_root: Path | str,
        home: Path | None = None,
        **kwargs: Any,
    ) -> HookDispatcher:
        return cls(load_hook_settings(project_root, home), project_root, **kwargs)

    def run(
        self,
        event: HookEvent | str,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        ctx_fields: dict[str, Any] | None = None,
        tool_response: str | None = None,
        command: str | None = None,
    ) -> HookOutcome:
        """
        Runs every hook configured for `event` that matches this tool call.

        The first denial in PreToolUse short-circuits to avoid unnecessary process spawns.
        """
        payload = self._payload(event, tool_name, tool_input, ctx_fields, tool_response)
        return self._dispatch_applicable(
            event=event,
            payload=payload,
            tool_name=tool_name,
            command=command,
            tool_input=tool_input,
        )

    def run_event(
        self,
        event: HookEvent | str,
        payload: dict[str, Any],
    ) -> HookOutcome:
        """
        Runs all hooks configured for an arbitrary lifecycle event (e.g. Stop, SessionStart).
        """
        tool_name = payload.get("tool_name")
        return self._dispatch_applicable(
            event=event,
            payload=payload,
            tool_name=str(tool_name) if tool_name is not None else None,
            command=payload.get("command"),
            tool_input=payload.get("tool_input"),
        )

    # ── Internals ────────────────────────────────────────────────────────────

    def _applicable(
        self,
        event: HookEvent | str,
        payload: dict[str, Any],
        tool_name: str | None,
        command: str | None,
        tool_input: dict[str, Any] | None,
    ) -> list[HookDefinition]:
        """
        Narrows the configured hooks to the ones matching the event, match query,
        and `if` condition. Deduplicates within the same source context.
        """
        event_str = str(event)
        match_query = extract_match_key(event_str, payload)

        candidates: list[HookDefinition] = []
        for matcher in self.settings.matchers_for(event_str):
            if not matches_pattern(match_query, matcher.matcher):
                continue
            for hook in matcher.hooks:
                if not eval_if_condition(hook.if_condition, tool_name, command, tool_input):
                    continue
                candidates.append(hook)

        # Deduplicate per Claude Code rules (H1)
        return list(deduplicate_hooks(candidates))

    def _payload(
        self,
        event: HookEvent | str,
        tool_name: str,
        tool_input: dict[str, Any],
        ctx_fields: dict[str, Any] | None,
        tool_response: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "hook_event_name": str(event),
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
        payload.update(ctx_fields or {})
        if tool_response is not None:
            payload["tool_response"] = tool_response
        return payload

    def _dispatch_applicable(
        self,
        event: HookEvent | str,
        payload: dict[str, Any],
        tool_name: str | None,
        command: str | None,
        tool_input: dict[str, Any] | None,
    ) -> HookOutcome:
        outcome = HookOutcome()
        contexts: list[str] = []
        blocking_errors_acc: list[str] = []
        event_str = str(event)

        applicable = self._applicable(event, payload, tool_name, command, tool_input)

        for hook in applicable:
            single = self._run_single_hook(hook, payload, event_str)

            if single.additional_context:
                contexts.append(single.additional_context)
            if single.updated_input is not None:
                outcome.updated_input = single.updated_input
            if single.stop_continuation:
                outcome.stop_continuation = True
            if single.prevent_continuation:
                outcome.prevent_continuation = True
            if single.blocking_errors:
                blocking_errors_acc.extend(single.blocking_errors)
            if single.denied:
                outcome.denied = True
                outcome.reason = single.reason
                # PreToolUse short-circuit: once vetoed, later hooks cannot un-veto
                if event_str == HookEvent.PRE_TOOL_USE.value:
                    break

        outcome.additional_context = "\n".join(contexts)
        outcome.blocking_errors = tuple(blocking_errors_acc)
        return outcome

    def _run_single_hook(
        self,
        hook: HookDefinition,
        payload: dict[str, Any],
        event: str,
    ) -> Any:
        if isinstance(hook, CommandHook):
            return self.command_backend.execute(hook, payload, event)
        if isinstance(hook, PromptHook):
            import asyncio

            # Synchronous bridge for PromptHook execution
            coro = self.prompt_backend.execute(hook, payload, event)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                # In an active event loop, run with nest_asyncio or runner
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(asyncio.run, coro).result()
            return asyncio.run(coro)

        if isinstance(hook, AgentHook):
            import asyncio

            coro = self.agent_backend.execute(hook, payload, event)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(asyncio.run, coro).result()
            return asyncio.run(coro)

        if isinstance(hook, HttpHook):
            return self.http_backend.execute(hook, payload, event)

        logger.warning("Unknown hook definition type: %s", type(hook))
        from app.hooks.backends import HookExecutionOutcome
        return HookExecutionOutcome()


__all__ = [
    "BLOCKING_EXIT_CODE",
    "HookEvent",
    "HookOutcome",
    "HookDispatcher",
    "_matches",
]
