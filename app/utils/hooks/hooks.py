"""
Running the hooks configured for an event, and the per-event entry points.

Ported from `reference/claude-code/src/utils/hooks.ts` -> `createBaseHookInput`,
`hasHookForEvent`, `executeHooks`, `executeHooksOutsideREPL`, `executePreToolHooks`,
`executePostToolHooks`, `executePostToolUseFailureHooks`, `executePermissionRequestHooks`,
`executeStopHooks`, `executeStopFailureHooks`, `executeUserPromptSubmitHooks`,
`executeSessionStartHooks`, `executeSubagentStartHooks`, `executePreCompactHooks`,
`executePostCompactHooks`, `executeSessionEndHooks`, `executeNotificationHooks`,
`executeElicitationHooks`, `executeElicitationResultHooks` (with `parseElicitationHookOutput`,
`ElicitationHookResult`, `ElicitationResultHookResult`).

`HookDispatcher` holds what upstream keeps in module state for a session — the frozen config
snapshot, the session id, the working and project directories, the async-hook registry — and
the three injected runners a prompt, agent or HTTP hook needs. One dispatcher per run, reached
through `ToolContext.hook_dispatcher`; a context without one runs no hooks.

`execute_hooks` is upstream's `executeHooks`: every matching hook runs **in parallel**, each
with its own timeout, and results are yielded **as they complete** as a stream of
`AggregatedHookResult`s. A PreToolUse permission verdict is aggregated with upstream's
precedence, deny > ask > allow. Trust is implicit: this loop only runs headless, and upstream
skips the trust check for non-interactive sessions (`shouldSkipHookDueToTrust`). Progress
messages and the stop-hook summary are UI and are not produced.

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

Running one command hook.

Ported from `reference/claude-code/src/utils/hooks.ts` -> `execCommandHook` (and the
`executeInBackground` call it makes).

* The command runs through the shell (`/bin/sh -c`, Node's `spawn(..., {shell: true})`), or
  through `pwsh -NoProfile -NonInteractive -Command` when `shell: "powershell"`.
* The hook input is one JSON line on stdin.
* Environment: the parent's, plus `TDDAGENTS_PROJECT_DIR` (upstream's `CLAUDE_PROJECT_DIR`,
  renamed) pointing at the project root.
* Working directory: the run's working directory (upstream's `getCwd()`, which for this
  loop is the workspace), falling back to the project root when it does not exist on this
  host — a remote sandbox's root, for instance (upstream falls back to `getOriginalCwd()`).
* Timeout: the hook's own `timeout` seconds, else ten minutes. A timed-out or aborted hook is
  killed and reported `aborted`, which the caller turns into `hook_cancelled`.
* Async: `async`/`asyncRewake` in the config, or a first stdout line that parses as
  `{"async": true}`, backgrounds the process (see `utils/hooks/async_hook_registry.py`) and returns at once.

A plugin's hook (`PluginHookContext`) runs with `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`
and `${user_config.X}` substituted in its command and `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`
and `CLAUDE_PLUGIN_OPTION_<KEY>` in its environment (the plugin format's names, kept verbatim).

Not ported: Windows path conversion and the `.sh` auto-prefix (this build is POSIX-only), a
skill hook's `CLAUDE_PLUGIN_ROOT` (`skillRoot`), `CLAUDE_ENV_FILE` (it feeds the persistent
shell environment upstream's Bash tool sources, which this Bash tool does not have),
`CLAUDE_CODE_SHELL_PREFIX`, and the interactive prompt-request protocol (a headless run has no
one to answer it).

A hook's child process, captured while it runs.

The Python side of upstream's `wrapSpawn` / `ShellCommand` / `TaskOutput` as hooks use them
(`reference/claude-code/src/utils/hooks.ts` -> `execCommandHook`; `src/utils/ShellCommand.ts`):
stdout and stderr are drained into buffers for as long as the process lives, so a hook that
was backgrounded keeps being captured after its caller moved on, and its output can be
collected when it finishes.

Cancellation is cooperative on this side: the run's `CancelToken` has no listeners, so the
wait polls it, and an aborted or timed-out hook is killed — upstream's combined abort signal
does the same to the child.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import json
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from app.schemas.hooks import (
    DEFAULT_HOOK_SHELL,
    AgentHook,
    CallbackHook,
    CommandHook,
    FunctionHook,
    HookCommand,
    HookMatcher,
    HttpHook,
    PromptHook,
)
from app.types.hooks import (
    AggregatedHookResult,
    HookResult,
    PromptRequest,
    PromptResponse,
    hook_result_from_json,
    is_async_hook_json_output,
    parse_hook_output,
    parse_http_hook_output,
    parse_prompt_request,
    validate_hook_json,
)
from app.utils.hooks.async_hook_registry import AsyncHookRegistry
from app.utils.hooks.hook_events import (
    HookEventEmitter,
    SESSION_END_HOOK_TIMEOUT_MS_DEFAULT,
    TOOL_EVENTS,
    TOOL_HOOK_EXECUTION_TIMEOUT_MS,
    HookEvent,
    match_query_for,
)
from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot
from app.utils.messages import HookAttachment, HookBlockingError, Message
from app.utils.settings.settings import (
    LEGACY_TOOL_NAME_ALIASES,
    normalize_legacy_tool_name,
    permission_rule_value_from_string,
)
from app.utils.shell_command import ABORT_POLL_SECONDS, CapturedProcess, is_aborted

logger = logging.getLogger("TDDOrchestrator.Hooks")

#: `executePromptHook`-style runner: `(hook, hook_name, hook_event, json_input, signal,
#: tool_context, messages, tool_use_id) -> HookResult`.
PromptHookExecutor = Callable[..., Awaitable[HookResult]]
#: `(hook, hook_name, hook_event, json_input, signal, tool_context, tool_use_id, messages,
#: agent_name) -> HookResult`.
AgentHookExecutor = Callable[..., Awaitable[HookResult]]
#: `(hook, hook_event, json_input, signal) -> HttpHookResult`.
HttpHookExecutor = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class HookOutsideReplResult:
    """`HookOutsideReplResult`."""

    command: str
    succeeded: bool
    output: str
    blocked: bool
    watch_paths: tuple[str, ...] = ()
    system_message: str | None = None


#: `requestPrompt(sourceName, toolInputSummary)`: the REPL's dialog, bound to who asks.
RequestPrompt = Callable[[str, str | None], Callable[[PromptRequest], Awaitable[PromptResponse]]]


@dataclass
class HookDispatcher:
    """The run's hook runtime: config snapshot, identity, directories and runners."""

    snapshot: HooksConfigSnapshot
    session_id: str
    cwd: str
    project_dir: str
    transcript_path: str = ""
    registry: AsyncHookRegistry = field(default_factory=AsyncHookRegistry)
    prompt_executor: PromptHookExecutor | None = None
    agent_executor: AgentHookExecutor | None = None
    http_executor: HttpHookExecutor | None = None
    #: Session-scoped matchers (an agent's or skill's frontmatter hooks, function hooks), by event.
    session_matchers: dict[str, list[HookMatcher]] = field(default_factory=dict)
    #: `registerHookCallbacks` (`bootstrap/state.ts`): an SDK host's callback hooks and the plugins'
    #: hooks (`app/utils/plugins/load_plugin_hooks.py`), by event.
    registered_matchers: dict[str, list[HookMatcher]] = field(default_factory=dict)
    #: The run's settings: where a plugin hook's saved options are read, and the plugins loaded.
    settings_locations: Any = None
    #: The plugin-hook generation this dispatcher registered (`loadPluginHooks`' memo, per run).
    plugin_hooks_generation: int | None = None
    #: `hookEvents.ts`: the run's hook lifecycle events (`hook_started` / `hook_response`).
    events: HookEventEmitter = field(default_factory=HookEventEmitter)
    #: Hook work started fire-and-forget (upstream's `void executeStopFailureHooks(...)`),
    #: kept so it is not garbage-collected and can be drained before the run ends.
    background: set[asyncio.Future[Any]] = field(default_factory=set)

    def spawn_background(self, work: Awaitable[Any]) -> None:
        """Start `work` without waiting for it (upstream's `void`)."""
        task = asyncio.ensure_future(work)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def drain_background(self) -> None:
        """
        Wait for fire-and-forget hook work. Upstream's process outlives the turn, so a detached
        hook finishes; here the run's event loop closes when the run ends, so the runner drains
        this first. A failure is logged, never raised.
        """
        while self.background:
            results = await asyncio.gather(*list(self.background), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    logger.warning("Background hook work failed: %s", result)

    # ── inputs ───────────────────────────────────────────────────────────────

    def create_base_hook_input(
        self,
        permission_mode: str | None = None,
        agent_id: str | None = None,
        agent_type: str | None = None,
    ) -> dict[str, Any]:
        """`createBaseHookInput`: the fields every hook input carries."""
        base: dict[str, Any] = {
            "session_id": self.session_id,
            "transcript_path": self.transcript_path,
            "cwd": self.cwd,
        }
        if permission_mode is not None:
            base["permission_mode"] = permission_mode
        if agent_id is not None:
            base["agent_id"] = agent_id
        if agent_type is not None:
            base["agent_type"] = agent_type
        return base

    def has_hook_for_event(self, event: str) -> bool:
        """`hasHookForEvent`: over-approximates, never under-approximates."""
        return (self.snapshot.has_hook_for_event(event) or bool(self.session_matchers.get(event))
                or bool(self.registered_matchers.get(event)))

    def register_hook_callbacks(self, hooks: Mapping[str, Sequence[HookMatcher]]) -> None:
        """`registerHookCallbacks`: add callback (or plugin) matchers to every later lookup."""
        for event, matchers in hooks.items():
            self.registered_matchers.setdefault(event, []).extend(matchers)

    def clear_registered_plugin_hooks(self) -> None:
        """`clearRegisteredPluginHooks`: drop the plugins' matchers, keep the SDK callbacks."""
        kept = {event: [m for m in matchers if m.plugin_root is None]
                for event, matchers in self.registered_matchers.items()}
        self.registered_matchers = {event: matchers for event, matchers in kept.items() if matchers}

    def _with_locations(self, plugin: PluginHookContext | None) -> PluginHookContext | None:
        if plugin is None or plugin.locations is not None:
            return plugin
        return replace(plugin, locations=self.settings_locations)

    def matching(
        self, hook_event: str, hook_input: Mapping[str, Any], tools: Sequence[Any] | None
    ) -> list[MatchedHook]:
        return get_matching_hooks(self.snapshot, hook_event, hook_input, tools, self.session_matchers,
                                  self.registered_matchers)

    # ── executeHooks ─────────────────────────────────────────────────────────

    async def execute_hooks(
        self,
        hook_input: Mapping[str, Any],
        tool_use_id: str,
        *,
        match_query: str | None = None,
        signal: Any = None,
        timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
        tool_context: Any = None,
        messages: Sequence[Message] | None = None,
        force_sync: bool = False,
        request_prompt: RequestPrompt | None = None,
        tool_input_summary: str | None = None,
    ) -> AsyncIterator[AggregatedHookResult]:
        """
        `executeHooks`: run the matching hooks in parallel and stream their news. `request_prompt`
        is bound to this hook's name and the tool's summary, so a dialog can say who asks; only
        command hooks receive it.
        """
        if self.snapshot.disable_all:
            return
        hook_event = str(hook_input["hook_event_name"])
        hook_name = f"{hook_event}:{match_query}" if match_query else hook_event
        tools = getattr(tool_context, "tools", None) if tool_context is not None else None
        matched = self.matching(hook_event, hook_input, tools)
        if not matched or is_aborted(signal):
            return
        json_input = json.dumps(hook_input)
        bound_prompt = request_prompt(hook_name, tool_input_summary) if request_prompt is not None else None

        async def run(index: int, m: MatchedHook) -> HookResult:
            return await self._run_one(
                m.hook, index, hook_event, hook_name, json_input, tool_use_id, signal,
                timeout_ms, tool_context, messages, force_sync,
                str(hook_input.get("agent_type")) if hook_input.get("agent_type") is not None else None,
                bound_prompt, plugin=_plugin_context(m),
            )

        permission_behavior: str | None = None
        sources = {id(m.hook): m.hook_source for m in matched}
        tasks = [asyncio.ensure_future(run(i, m)) for i, m in enumerate(matched)]
        try:
            for finished in asyncio.as_completed(tasks):
                result = await finished
                for item in _aggregate(result):
                    yield item
                if result.permission_behavior is not None:
                    permission_behavior = _precedence(permission_behavior, result.permission_behavior)
                # Upstream yields the *aggregate* once any hook has decided, with this
                # hook's reason and rewritten input, after every later result too.
                if permission_behavior is not None:
                    updated = (
                        result.updated_input
                        if result.updated_input and result.permission_behavior in ("allow", "ask")
                        else None
                    )
                    yield AggregatedHookResult(
                        permission_behavior=permission_behavior,
                        hook_permission_decision_reason=result.hook_permission_decision_reason,
                        hook_source=sources.get(id(result.hook)),
                        updated_input=updated,
                    )
                for item in _aggregate_tail(result):
                    yield item
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    async def _run_one(
        self,
        hook: HookCommand,
        index: int,
        hook_event: str,
        hook_name: str,
        json_input: str,
        tool_use_id: str,
        signal: Any,
        timeout_ms: int,
        tool_context: Any,
        messages: Sequence[Message] | None,
        force_sync: bool,
        agent_type: str | None,
        request_prompt: Callable[[PromptRequest], Awaitable[PromptResponse]] | None = None,
        *,
        plugin: PluginHookContext | None = None,
    ) -> HookResult:
        """One hook of `executeHooks`' parallel batch. Never raises."""
        started = time.monotonic()
        command_text = hook_display_text(hook)

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        def non_blocking(stderr: str, stdout: str = "", exit_code: int = 1) -> HookResult:
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
                    exit_code=exit_code,
                    command=command_text,
                    duration_ms=elapsed(),
                ),
            )

        try:
            if isinstance(hook, PromptHook):
                if self.prompt_executor is None:
                    raise RuntimeError("No prompt hook executor is configured for this run.")
                result = await self.prompt_executor(
                    hook, hook_name, hook_event, json_input, signal, tool_context, messages, tool_use_id
                )
                return _with_timing(result, command_text, elapsed())
            if isinstance(hook, AgentHook):
                if self.agent_executor is None:
                    raise RuntimeError("No agent hook executor is configured for this run.")
                if messages is None:
                    raise RuntimeError("Messages are required for agent hooks. This is a bug.")
                result = await self.agent_executor(
                    hook, hook_name, hook_event, json_input, signal, tool_context, tool_use_id, messages, agent_type
                )
                return _with_timing(result, command_text, elapsed())
            if isinstance(hook, (HttpHook, CommandHook)):
                return await self._run_reported(
                    hook, index, hook_event, hook_name, json_input, tool_use_id, signal, timeout_ms, force_sync,
                    started, request_prompt, plugin=plugin)
            if isinstance(hook, CallbackHook):
                return await self._run_callback(hook, hook_event, json_input, tool_use_id, signal, timeout_ms)
            if isinstance(hook, FunctionHook):
                return await self._run_function(hook, hook_event, hook_name, tool_use_id, signal, timeout_ms,
                                                messages)
            return await self._run_command(
                hook, index, hook_event, hook_name, json_input, tool_use_id, signal, timeout_ms, force_sync, started,
                request_prompt, plugin=plugin,
            )
        except Exception as exc:  # noqa: BLE001 - upstream turns any failure into a non-blocking error
            return non_blocking(f"Failed to run: {exc}")

    async def _run_reported(
        self, hook: HttpHook | CommandHook, index: int, hook_event: str, hook_name: str, json_input: str,
        tool_use_id: str, signal: Any, timeout_ms: int, force_sync: bool, started: float,
        request_prompt: Callable[[PromptRequest], Awaitable[PromptResponse]] | None,
        *,
        plugin: PluginHookContext | None = None,
    ) -> HookResult:
        """A command or HTTP hook between `emitHookStarted` and `emitHookResponse`."""
        hook_id = str(uuid.uuid4())
        self.events.emit_started(hook_id, hook_name, hook_event)
        if isinstance(hook, HttpHook):
            result = await self._run_http(hook, hook_event, hook_name, json_input, tool_use_id, signal)
        else:
            result = await self._run_command(
                hook, index, hook_event, hook_name, json_input, tool_use_id, signal, timeout_ms, force_sync, started,
                request_prompt, plugin=plugin,
            )
        attachment = result.attachment
        stdout = (attachment.stdout if attachment is not None else None) or ""
        stderr = (attachment.stderr if attachment is not None else None) or ""
        if result.blocking_error is not None and not stderr:
            stderr = result.blocking_error.blocking_error
        outcome = {"cancelled": "cancelled", "success": "success"}.get(result.outcome, "error")
        self.events.emit_response(
            hook_id=hook_id, hook_name=hook_name, hook_event=hook_event, output=stdout + stderr, stdout=stdout,
            stderr=stderr, exit_code=attachment.exit_code if attachment is not None else None, outcome=outcome)
        return result

    async def _run_callback(
        self, hook: CallbackHook, hook_event: str, json_input: str, tool_use_id: str, signal: Any, timeout_ms: int,
    ) -> HookResult:
        """`executeHookCallback`: the callback's JSON output, processed as a command hook's."""
        timeout = hook.timeout if hook.timeout else timeout_ms / 1000
        output = await asyncio.wait_for(hook.callback(json.loads(json_input), tool_use_id, signal), timeout)
        if is_async_hook_json_output(output):
            return HookResult(hook=hook, outcome="success")
        processed = hook_result_from_json(
            hook, dict(output), command="callback", hook_name=f"{hook_event}:Callback", tool_use_id=tool_use_id,
            hook_event=hook_event, expected_hook_event=hook_event, stdout=None, stderr=None, exit_code=None)
        return replace(processed, outcome="success")

    async def _run_function(
        self, hook: FunctionHook, hook_event: str, hook_name: str, tool_use_id: str, signal: Any, timeout_ms: int,
        messages: Sequence[Message] | None,
    ) -> HookResult:
        """`executeFunctionHook`: the callback judges the messages; false blocks with its message."""
        def error(content: str) -> HookResult:
            return HookResult(hook=hook, outcome="non_blocking_error", attachment=HookAttachment(
                type="hook_error_during_execution", hook_name=hook_name, tool_use_id=tool_use_id,
                hook_event=hook_event, content=content))

        if messages is None:
            return error("Messages not provided for function hook")
        if is_aborted(signal):
            return HookResult(hook=hook, outcome="cancelled")
        timeout = (hook.timeout if hook.timeout is not None else timeout_ms) / 1000
        try:
            answer = hook.callback(list(messages))
            passed = await asyncio.wait_for(answer, timeout) if inspect.isawaitable(answer) else answer
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return HookResult(hook=hook, outcome="cancelled")
        except Exception as exc:  # noqa: BLE001 - upstream logs it and reports a non-blocking error
            logger.error("Function hook failed: %s", exc)
            return error(str(exc) or "Function hook execution error")
        if passed:
            return HookResult(hook=hook, outcome="success")
        return HookResult(hook=hook, outcome="blocking",
                          blocking_error=HookBlockingError(blocking_error=hook.error_message, command="function"))

    async def _run_http(
        self, hook: HttpHook, hook_event: str, hook_name: str, json_input: str, tool_use_id: str, signal: Any
    ) -> HookResult:
        if self.http_executor is None:
            raise RuntimeError("No HTTP hook executor is configured for this run.")
        http = await self.http_executor(hook, hook_event, json_input, signal)
        if http.aborted:
            return HookResult(
                hook=hook,
                outcome="cancelled",
                attachment=HookAttachment(
                    type="hook_cancelled", hook_name=hook_name, tool_use_id=tool_use_id, hook_event=hook_event
                ),
            )
        if http.error or not http.ok:
            stderr = http.error or f"HTTP {http.status_code} from {hook.url}"
            return HookResult(
                hook=hook,
                outcome="non_blocking_error",
                attachment=HookAttachment(
                    type="hook_non_blocking_error", hook_name=hook_name, tool_use_id=tool_use_id,
                    hook_event=hook_event, stderr=stderr, stdout="", exit_code=http.status_code or 0,
                ),
            )
        parsed = parse_http_hook_output(http.body)
        if parsed.validation_error:
            return HookResult(
                hook=hook,
                outcome="non_blocking_error",
                attachment=HookAttachment(
                    type="hook_non_blocking_error", hook_name=hook_name, tool_use_id=tool_use_id,
                    hook_event=hook_event, stderr=f"JSON validation failed: {parsed.validation_error}",
                    stdout=http.body, exit_code=http.status_code or 0,
                ),
            )
        assert parsed.json is not None
        if is_async_hook_json_output(parsed.json):
            return HookResult(hook=hook, outcome="success")
        return hook_result_from_json(
            hook,
            parsed.json,
            command=hook.url,
            hook_name=hook_name,
            tool_use_id=tool_use_id,
            hook_event=hook_event,
            expected_hook_event=hook_event,
            stdout=http.body,
            stderr="",
            exit_code=http.status_code,
        )

    async def _run_command(
        self,
        hook: CommandHook,
        index: int,
        hook_event: str,
        hook_name: str,
        json_input: str,
        tool_use_id: str,
        signal: Any,
        timeout_ms: int,
        force_sync: bool,
        started: float,
        request_prompt: Callable[[PromptRequest], Awaitable[PromptResponse]] | None = None,
        *,
        plugin: PluginHookContext | None = None,
    ) -> HookResult:
        command_timeout = int(hook.timeout * 1000) if hook.timeout else timeout_ms
        result: CommandHookResult = await exec_command_hook(
            hook, hook_event, hook_name, json_input, signal,
            cwd=self.cwd, project_dir=self.project_dir, registry=self.registry,
            timeout_ms=command_timeout, force_sync=force_sync, request_prompt=request_prompt,
            plugin=self._with_locations(plugin),
        )
        duration = int((time.monotonic() - started) * 1000)
        command = hook_display_text(hook)

        def attachment(kind: str, **fields: Any) -> HookAttachment:
            return HookAttachment(
                type=kind, hook_name=hook_name, tool_use_id=tool_use_id, hook_event=hook_event,
                command=command, duration_ms=duration, **fields,
            )

        if result.backgrounded:
            return HookResult(hook=hook, outcome="success")
        if result.aborted:
            return HookResult(
                hook=hook,
                outcome="cancelled",
                attachment=attachment("hook_cancelled"),
            )
        parsed = parse_hook_output(result.stdout)
        if parsed.validation_error:
            return HookResult(
                hook=hook,
                outcome="non_blocking_error",
                attachment=attachment(
                    "hook_non_blocking_error", stderr=f"JSON validation failed: {parsed.validation_error}",
                    stdout=result.stdout, exit_code=1,
                ),
            )
        if parsed.json is not None:
            if is_async_hook_json_output(parsed.json):
                return HookResult(hook=hook, outcome="success")
            return hook_result_from_json(
                hook,
                parsed.json,
                command=command,
                hook_name=hook_name,
                tool_use_id=tool_use_id,
                hook_event=hook_event,
                expected_hook_event=hook_event,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.status,
                duration_ms=duration,
            )
        if result.status == 0:
            return HookResult(
                hook=hook,
                outcome="success",
                attachment=attachment(
                    "hook_success", content=result.stdout.strip(), stdout=result.stdout,
                    stderr=result.stderr, exit_code=0,
                ),
            )
        if result.status == 2:
            return HookResult(
                hook=hook,
                outcome="blocking",
                blocking_error=HookBlockingError(
                    blocking_error=f"[{hook.command}]: {result.stderr or 'No stderr output'}",
                    command=hook.command,
                ),
            )
        return HookResult(
            hook=hook,
            outcome="non_blocking_error",
            attachment=attachment(
                "hook_non_blocking_error",
                stderr=f"Failed with non-blocking status code: {result.stderr.strip() or 'No stderr output'}",
                stdout=result.stdout, exit_code=result.status,
            ),
        )

    # ── executeHooksOutsideREPL ──────────────────────────────────────────────

    async def execute_hooks_outside_repl(
        self,
        hook_input: Mapping[str, Any],
        *,
        match_query: str | None = None,
        signal: Any = None,
        timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
    ) -> list[HookOutsideReplResult]:
        """
        `executeHooksOutsideREPL`: for events whose hooks report rather than steer (PreCompact,
        PostCompact, SessionEnd, StopFailure). Prompt and agent hooks are not supported here,
        exactly as upstream.
        """
        if self.snapshot.disable_all:
            return []
        hook_event = str(hook_input["hook_event_name"])
        hook_name = f"{hook_event}:{match_query}" if match_query else hook_event
        matched = self.matching(hook_event, hook_input, None)
        if not matched or is_aborted(signal):
            return []
        json_input = json.dumps(hook_input)

        async def run(index: int, matched_hook: MatchedHook) -> HookOutsideReplResult:
            hook = matched_hook.hook
            if isinstance(hook, PromptHook):
                return HookOutsideReplResult(
                    command=hook.prompt, succeeded=False,
                    output="Prompt stop hooks are not yet supported outside REPL", blocked=False,
                )
            if isinstance(hook, AgentHook):
                return HookOutsideReplResult(
                    command=hook.prompt, succeeded=False,
                    output="Agent stop hooks are not yet supported outside REPL", blocked=False,
                )
            if isinstance(hook, HttpHook):
                return await self._outside_http(hook, hook_event, json_input, signal)
            if isinstance(hook, CallbackHook):
                return await self._outside_callback(hook, hook_event, json_input, signal, timeout_ms)
            if isinstance(hook, FunctionHook):
                return HookOutsideReplResult(command="function", succeeded=False,
                                             output="Internal error: function hook executed outside REPL context",
                                             blocked=False)
            return await self._outside_command(hook, hook_event, hook_name, json_input, signal, timeout_ms,
                                               plugin=_plugin_context(matched_hook))

        return list(await asyncio.gather(*(run(i, m) for i, m in enumerate(matched))))

    async def _outside_callback(
        self, hook: CallbackHook, hook_event: str, json_input: str, signal: Any, timeout_ms: int
    ) -> HookOutsideReplResult:
        """`executeHooksOutsideREPL`'s callback branch."""
        timeout = hook.timeout if hook.timeout else timeout_ms / 1000
        try:
            output = await asyncio.wait_for(hook.callback(json.loads(json_input), str(uuid.uuid4()), signal), timeout)
        except Exception as exc:  # noqa: BLE001 - upstream reports the failure as the hook's output
            return HookOutsideReplResult(command="callback", succeeded=False, output=str(exc), blocked=False)
        if is_async_hook_json_output(output):
            return HookOutsideReplResult(command="callback", succeeded=True, output="", blocked=False)
        specific = output.get("hookSpecificOutput") or {}
        text = (specific.get("worktreePath") if hook_event == "WorktreeCreate"
                and specific.get("hookEventName") == "WorktreeCreate" else output.get("systemMessage")) or ""
        return HookOutsideReplResult(command="callback", succeeded=True, output=str(text),
                                     blocked=output.get("decision") == "block")

    async def _outside_http(
        self, hook: HttpHook, hook_event: str, json_input: str, signal: Any
    ) -> HookOutsideReplResult:
        try:
            if self.http_executor is None:
                raise RuntimeError("No HTTP hook executor is configured for this run.")
            http = await self.http_executor(hook, hook_event, json_input, signal)
            if http.aborted:
                return HookOutsideReplResult(command=hook.url, succeeded=False, output="Hook cancelled", blocked=False)
            if http.error or not http.ok:
                message = http.error or f"HTTP {http.status_code} from {hook.url}"
                return HookOutsideReplResult(command=hook.url, succeeded=False, output=message, blocked=False)
            parsed = parse_http_hook_output(http.body)
            if parsed.validation_error:
                raise ValueError(parsed.validation_error)
            blocked = bool(
                parsed.json and not is_async_hook_json_output(parsed.json) and parsed.json.get("decision") == "block"
            )
            output = http.body
            if hook_event == HookEvent.WORKTREE_CREATE:
                specific = (parsed.json or {}).get("hookSpecificOutput") or {}
                is_worktree = specific.get("hookEventName") == "WorktreeCreate"
                output = str(specific.get("worktreePath", "")) if is_worktree else ""
            return HookOutsideReplResult(command=hook.url, succeeded=True, output=output, blocked=blocked)
        except Exception as exc:  # noqa: BLE001
            return HookOutsideReplResult(command=hook.url, succeeded=False, output=str(exc), blocked=False)

    async def _outside_command(
        self, hook: CommandHook, hook_event: str, hook_name: str, json_input: str, signal: Any, timeout_ms: int,
        *, plugin: PluginHookContext | None = None,
    ) -> HookOutsideReplResult:
        command_timeout = int(hook.timeout * 1000) if hook.timeout else timeout_ms
        try:
            result = await exec_command_hook(
                hook, hook_event, hook_name, json_input, signal,
                cwd=self.cwd, project_dir=self.project_dir, registry=self.registry, timeout_ms=command_timeout,
                plugin=self._with_locations(plugin),
            )
            if result.aborted:
                return HookOutsideReplResult(
                    command=hook.command, succeeded=False, output="Hook cancelled", blocked=False
                )
            parsed = parse_hook_output(result.stdout)
            if parsed.validation_error:
                raise ValueError(parsed.validation_error)
            sync_json = parsed.json if parsed.json is not None and not is_async_hook_json_output(parsed.json) else None
            blocked = result.status == 2 or bool(sync_json and sync_json.get("decision") == "block")
            output = (result.stdout or "") if result.status == 0 else (result.stderr or "")
            watch_paths: tuple[str, ...] = ()
            system_message = None
            if sync_json is not None:
                specific = sync_json.get("hookSpecificOutput")
                if isinstance(specific, Mapping) and "watchPaths" in specific:
                    watch_paths = tuple(specific.get("watchPaths") or ())
                system_message = sync_json.get("systemMessage")
            return HookOutsideReplResult(
                command=hook.command, succeeded=result.status == 0, output=output, blocked=blocked,
                watch_paths=watch_paths, system_message=system_message,
            )
        except Exception as exc:  # noqa: BLE001
            return HookOutsideReplResult(command=hook.command, succeeded=False, output=str(exc), blocked=False)


def _with_timing(result: HookResult, command: str, duration_ms: int) -> HookResult:
    """Upstream injects `command` and `durationMs` into a prompt/agent hook's attachment."""
    attachment = result.attachment
    if attachment is not None and attachment.type in ("hook_success", "hook_non_blocking_error"):
        return replace(result, attachment=replace(attachment, command=command, duration_ms=duration_ms))
    return result


def _precedence(current: str | None, new: str) -> str | None:
    """`executeHooks`' permission precedence: deny > ask > allow; passthrough sets nothing."""
    if new == "deny":
        return "deny"
    if new == "ask":
        return current if current == "deny" else "ask"
    if new == "allow":
        return current if current is not None else "allow"
    return current


def _aggregate(result: HookResult) -> list[AggregatedHookResult]:
    """The items `executeHooks` yields for one finished hook before the permission item."""
    items: list[AggregatedHookResult] = []
    if result.prevent_continuation:
        items.append(AggregatedHookResult(prevent_continuation=True, stop_reason=result.stop_reason))
    if result.blocking_error is not None:
        items.append(AggregatedHookResult(blocking_error=result.blocking_error))
    if result.attachment is not None:
        items.append(AggregatedHookResult(attachment=result.attachment))
    if result.system_message:
        source = result.attachment
        items.append(
            AggregatedHookResult(
                attachment=HookAttachment(
                    type="hook_system_message",
                    hook_name=source.hook_name if source else "",
                    tool_use_id=source.tool_use_id if source else "",
                    hook_event=source.hook_event if source else "",
                    content=result.system_message,
                )
            )
        )
    if result.additional_context:
        items.append(AggregatedHookResult(additional_contexts=(result.additional_context,)))
    if result.initial_user_message:
        items.append(AggregatedHookResult(initial_user_message=result.initial_user_message))
    if result.watch_paths:
        items.append(AggregatedHookResult(watch_paths=result.watch_paths))
    if result.updated_mcp_tool_output:
        items.append(AggregatedHookResult(updated_mcp_tool_output=result.updated_mcp_tool_output))
    return items


def _aggregate_tail(result: HookResult) -> list[AggregatedHookResult]:
    """The items `executeHooks` yields for one finished hook after the permission item."""
    items: list[AggregatedHookResult] = []
    # A rewrite with no permission opinion: the passthrough `updatedInput`.
    if result.updated_input and result.permission_behavior is None:
        items.append(AggregatedHookResult(updated_input=result.updated_input))
    if result.permission_request_result:
        items.append(AggregatedHookResult(permission_request_result=result.permission_request_result))
    if result.retry:
        items.append(AggregatedHookResult(retry=result.retry))
    return items


# ── per-event entry points ──────────────────────────────────────────────────────


def dispatcher_of(context: Any) -> HookDispatcher | None:
    dispatcher = getattr(context, "hook_dispatcher", None)
    return dispatcher if isinstance(dispatcher, HookDispatcher) else None


def _agent_fields(context: Any) -> tuple[str | None, str | None]:
    return getattr(context, "agent_id", None), getattr(context, "agent_type", None)


async def _empty() -> AsyncIterator[AggregatedHookResult]:
    return
    yield  # pragma: no cover - makes this an async generator


def execute_pre_tool_hooks(
    context: Any,
    tool_name: str,
    tool_use_id: str,
    tool_input: Mapping[str, Any],
    permission_mode: str | None,
    signal: Any = None,
    tool_input_summary: str | None = None,
) -> AsyncIterator[AggregatedHookResult]:
    """`executePreToolHooks`: the context's `request_prompt` goes along with the tool's summary."""
    dispatcher = dispatcher_of(context)
    if dispatcher is None or not dispatcher.has_hook_for_event(HookEvent.PRE_TOOL_USE):
        return _empty()
    agent_id, agent_type = _agent_fields(context)
    hook_input = {
        **dispatcher.create_base_hook_input(permission_mode, agent_id, agent_type),
        "hook_event_name": HookEvent.PRE_TOOL_USE.value,
        "tool_name": tool_name,
        "tool_input": dict(tool_input),
        "tool_use_id": tool_use_id,
    }
    return dispatcher.execute_hooks(
        hook_input, tool_use_id, match_query=tool_name, signal=signal, tool_context=context,
        request_prompt=getattr(context, "request_prompt", None), tool_input_summary=tool_input_summary,
    )


def execute_post_tool_hooks(
    context: Any,
    tool_name: str,
    tool_use_id: str,
    tool_input: Mapping[str, Any],
    tool_response: Any,
    permission_mode: str | None,
    signal: Any = None,
) -> AsyncIterator[AggregatedHookResult]:
    """`executePostToolHooks`."""
    dispatcher = dispatcher_of(context)
    if dispatcher is None or not dispatcher.has_hook_for_event(HookEvent.POST_TOOL_USE):
        return _empty()
    agent_id, agent_type = _agent_fields(context)
    hook_input = {
        **dispatcher.create_base_hook_input(permission_mode, agent_id, agent_type),
        "hook_event_name": HookEvent.POST_TOOL_USE.value,
        "tool_name": tool_name,
        "tool_input": dict(tool_input),
        "tool_response": tool_response,
        "tool_use_id": tool_use_id,
    }
    return dispatcher.execute_hooks(
        hook_input, tool_use_id, match_query=tool_name, signal=signal, tool_context=context
    )


def execute_post_tool_use_failure_hooks(
    context: Any,
    tool_name: str,
    tool_use_id: str,
    tool_input: Mapping[str, Any],
    error: str,
    is_interrupt: bool | None,
    permission_mode: str | None,
    signal: Any = None,
) -> AsyncIterator[AggregatedHookResult]:
    """`executePostToolUseFailureHooks`."""
    dispatcher = dispatcher_of(context)
    if dispatcher is None or not dispatcher.has_hook_for_event(HookEvent.POST_TOOL_USE_FAILURE):
        return _empty()
    agent_id, agent_type = _agent_fields(context)
    hook_input: dict[str, Any] = {
        **dispatcher.create_base_hook_input(permission_mode, agent_id, agent_type),
        "hook_event_name": HookEvent.POST_TOOL_USE_FAILURE.value,
        "tool_name": tool_name,
        "tool_input": dict(tool_input),
        "tool_use_id": tool_use_id,
        "error": error,
    }
    if is_interrupt is not None:
        hook_input["is_interrupt"] = is_interrupt
    return dispatcher.execute_hooks(
        hook_input, tool_use_id, match_query=tool_name, signal=signal, tool_context=context
    )


def execute_permission_request_hooks(
    context: Any,
    tool_name: str,
    tool_use_id: str,
    tool_input: Mapping[str, Any],
    permission_mode: str | None,
    permission_suggestions: Sequence[Any] | None = None,
    signal: Any = None,
) -> AsyncIterator[AggregatedHookResult]:
    """`executePermissionRequestHooks`."""
    dispatcher = dispatcher_of(context)
    if dispatcher is None:
        return _empty()
    agent_id, agent_type = _agent_fields(context)
    hook_input: dict[str, Any] = {
        **dispatcher.create_base_hook_input(permission_mode, agent_id, agent_type),
        "hook_event_name": HookEvent.PERMISSION_REQUEST.value,
        "tool_name": tool_name,
        "tool_input": dict(tool_input),
    }
    if permission_suggestions is not None:
        hook_input["permission_suggestions"] = list(permission_suggestions)
    return dispatcher.execute_hooks(
        hook_input, tool_use_id, match_query=tool_name, signal=signal, tool_context=context
    )


def execute_permission_denied_hooks(
    context: Any,
    tool_name: str,
    tool_use_id: str,
    tool_input: Mapping[str, Any],
    reason: str,
    permission_mode: str | None,
    signal: Any = None,
) -> AsyncIterator[AggregatedHookResult]:
    """`executePermissionDeniedHooks`: after the auto-mode classifier refused a call."""
    dispatcher = dispatcher_of(context)
    if dispatcher is None or not dispatcher.has_hook_for_event(HookEvent.PERMISSION_DENIED):
        return _empty()
    agent_id, agent_type = _agent_fields(context)
    hook_input = {
        **dispatcher.create_base_hook_input(permission_mode, agent_id, agent_type),
        "hook_event_name": HookEvent.PERMISSION_DENIED.value,
        "tool_name": tool_name,
        "tool_input": dict(tool_input),
        "tool_use_id": tool_use_id,
        "reason": reason,
    }
    return dispatcher.execute_hooks(
        hook_input, tool_use_id, match_query=tool_name, signal=signal, tool_context=context
    )


def last_assistant_text(messages: Sequence[Message] | None) -> str | None:
    """The last assistant message's text, trimmed, or `None` (`getLastAssistantMessage`)."""
    for message in reversed(list(messages or ())):
        if getattr(message, "type", None) != "ai":
            continue
        content = message.content
        if isinstance(content, str):
            text = content
        else:
            text = "\n".join(
                str(block.get("text", "")) if isinstance(block, Mapping) else str(block)
                for block in content
                if not isinstance(block, Mapping) or block.get("type") == "text"
            )
        return text.strip() or None
    return None


def execute_stop_hooks(
    context: Any,
    permission_mode: str | None,
    signal: Any,
    stop_hook_active: bool,
    messages: Sequence[Message],
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
) -> AsyncIterator[AggregatedHookResult]:
    """`executeStopHooks`: `SubagentStop` inside a worker (an `agent_id`), else `Stop`."""
    dispatcher = dispatcher_of(context)
    agent_id, agent_type = _agent_fields(context)
    hook_event = HookEvent.SUBAGENT_STOP if agent_id else HookEvent.STOP
    if dispatcher is None or not dispatcher.has_hook_for_event(hook_event):
        return _empty()
    text = last_assistant_text(messages)
    hook_input: dict[str, Any] = {
        **dispatcher.create_base_hook_input(permission_mode),
        "hook_event_name": hook_event.value,
        "stop_hook_active": stop_hook_active,
    }
    if agent_id:
        hook_input.update(
            {"agent_id": agent_id, "agent_transcript_path": "", "agent_type": agent_type or ""}
        )
    if text is not None:
        hook_input["last_assistant_message"] = text
    return dispatcher.execute_hooks(
        hook_input, str(uuid.uuid4()), signal=signal, timeout_ms=timeout_ms, tool_context=context, messages=messages
    )


def execute_user_prompt_submit_hooks(
    context: Any, prompt: str, permission_mode: str
) -> AsyncIterator[AggregatedHookResult]:
    """`executeUserPromptSubmitHooks`."""
    dispatcher = dispatcher_of(context)
    if dispatcher is None or not dispatcher.has_hook_for_event(HookEvent.USER_PROMPT_SUBMIT):
        return _empty()
    hook_input = {
        **dispatcher.create_base_hook_input(permission_mode),
        "hook_event_name": HookEvent.USER_PROMPT_SUBMIT.value,
        "prompt": prompt,
    }
    return dispatcher.execute_hooks(
        hook_input, str(uuid.uuid4()), signal=getattr(context, "cancel", None), tool_context=context,
        request_prompt=getattr(context, "request_prompt", None),
    )


def execute_session_start_hooks(
    dispatcher: HookDispatcher | None,
    source: str,
    agent_type: str | None = None,
    model: str | None = None,
    signal: Any = None,
    force_sync: bool = False,
) -> AsyncIterator[AggregatedHookResult]:
    """`executeSessionStartHooks`."""
    if dispatcher is None:
        return _empty()
    hook_input: dict[str, Any] = {
        **dispatcher.create_base_hook_input(None),
        "hook_event_name": HookEvent.SESSION_START.value,
        "source": source,
    }
    if agent_type is not None:
        hook_input["agent_type"] = agent_type
    if model is not None:
        hook_input["model"] = model
    return dispatcher.execute_hooks(
        hook_input, str(uuid.uuid4()), match_query=source, signal=signal, force_sync=force_sync
    )


def execute_subagent_start_hooks(
    context: Any, agent_id: str, agent_type: str, signal: Any = None
) -> AsyncIterator[AggregatedHookResult]:
    """`executeSubagentStartHooks`."""
    dispatcher = dispatcher_of(context)
    if dispatcher is None:
        return _empty()
    hook_input = {
        **dispatcher.create_base_hook_input(None),
        "hook_event_name": HookEvent.SUBAGENT_START.value,
        "agent_id": agent_id,
        "agent_type": agent_type,
    }
    return dispatcher.execute_hooks(hook_input, str(uuid.uuid4()), match_query=agent_type, signal=signal)


def _display(event: str, results: Sequence[HookOutsideReplResult]) -> str | None:
    lines: list[str] = []
    for r in results:
        out = r.output.strip()
        if r.succeeded:
            lines.append(f"{event} [{r.command}] completed successfully" + (f": {out}" if out else ""))
        else:
            lines.append(f"{event} [{r.command}] failed" + (f": {out}" if out else ""))
    return "\n".join(lines) if lines else None


@dataclass(frozen=True, slots=True)
class PreCompactHookResult:
    """`executePreCompactHooks`' result."""

    new_custom_instructions: str | None = None
    user_display_message: str | None = None


async def execute_pre_compact_hooks(
    dispatcher: HookDispatcher | None,
    trigger: str,
    custom_instructions: str | None,
    signal: Any = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
) -> PreCompactHookResult:
    """`executePreCompactHooks`: successful outputs become extra compaction instructions."""
    if dispatcher is None:
        return PreCompactHookResult()
    hook_input = {
        **dispatcher.create_base_hook_input(None),
        "hook_event_name": HookEvent.PRE_COMPACT.value,
        "trigger": trigger,
        "custom_instructions": custom_instructions,
    }
    results = await dispatcher.execute_hooks_outside_repl(
        hook_input, match_query=trigger, signal=signal, timeout_ms=timeout_ms
    )
    if not results:
        return PreCompactHookResult()
    outputs = [r.output.strip() for r in results if r.succeeded and r.output.strip()]
    return PreCompactHookResult(
        new_custom_instructions="\n\n".join(outputs) if outputs else None,
        user_display_message=_display("PreCompact", results),
    )


async def execute_post_compact_hooks(
    dispatcher: HookDispatcher | None,
    trigger: str,
    compact_summary: str,
    signal: Any = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
) -> str | None:
    """`executePostCompactHooks`: the display message, or `None`."""
    if dispatcher is None:
        return None
    hook_input = {
        **dispatcher.create_base_hook_input(None),
        "hook_event_name": HookEvent.POST_COMPACT.value,
        "trigger": trigger,
        "compact_summary": compact_summary,
    }
    results = await dispatcher.execute_hooks_outside_repl(
        hook_input, match_query=trigger, signal=signal, timeout_ms=timeout_ms
    )
    return _display("PostCompact", results) if results else None


async def execute_session_end_hooks(
    dispatcher: HookDispatcher | None,
    reason: str,
    signal: Any = None,
    timeout_ms: int = SESSION_END_HOOK_TIMEOUT_MS_DEFAULT,
) -> list[HookOutsideReplResult]:
    """`executeSessionEndHooks`: failures are reported (upstream writes them to stderr)."""
    if dispatcher is None:
        return []
    hook_input = {
        **dispatcher.create_base_hook_input(None),
        "hook_event_name": HookEvent.SESSION_END.value,
        "reason": reason,
    }
    results = await dispatcher.execute_hooks_outside_repl(
        hook_input, match_query=reason, signal=signal, timeout_ms=timeout_ms
    )
    for result in results:
        if not result.succeeded and result.output:
            logger.warning("SessionEnd hook [%s] failed: %s", result.command, result.output)
    return results


async def execute_stop_failure_hooks(
    context: Any,
    error: str | None,
    error_details: str | None,
    messages: Sequence[Message],
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
) -> None:
    """`executeStopFailureHooks`: fire-and-report when a turn ends on an API error."""
    dispatcher = dispatcher_of(context)
    if dispatcher is None or not dispatcher.has_hook_for_event(HookEvent.STOP_FAILURE):
        return
    resolved = error or "unknown"
    agent_id, agent_type = _agent_fields(context)
    hook_input: dict[str, Any] = {
        **dispatcher.create_base_hook_input(None, agent_id, agent_type),
        "hook_event_name": HookEvent.STOP_FAILURE.value,
        "error": resolved,
    }
    if error_details is not None:
        hook_input["error_details"] = error_details
    text = last_assistant_text(messages)
    if text is not None:
        hook_input["last_assistant_message"] = text
    await dispatcher.execute_hooks_outside_repl(hook_input, match_query=resolved, timeout_ms=timeout_ms)


async def execute_notification_hooks(
    dispatcher: HookDispatcher | None,
    message: str,
    notification_type: str,
    title: str | None = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
) -> None:
    """`executeNotificationHooks`: report a notification to the hooks matching its type."""
    if dispatcher is None:
        return
    hook_input: dict[str, Any] = {
        **dispatcher.create_base_hook_input(None),
        "hook_event_name": HookEvent.NOTIFICATION.value,
        "message": message,
        "notification_type": notification_type,
    }
    if title is not None:
        hook_input["title"] = title
    await dispatcher.execute_hooks_outside_repl(hook_input, match_query=notification_type, timeout_ms=timeout_ms)


@dataclass(frozen=True, slots=True)
class ElicitationResponse:
    """`ElicitationResponse`: the `action` (accept, decline, cancel) and `content` a hook gives."""

    action: str
    content: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ElicitationHookResult:
    """`ElicitationHookResult`."""

    elicitation_response: ElicitationResponse | None = None
    blocking_error: HookBlockingError | None = None


@dataclass(frozen=True, slots=True)
class ElicitationResultHookResult:
    """`ElicitationResultHookResult`."""

    elicitation_result_response: ElicitationResponse | None = None
    blocking_error: HookBlockingError | None = None


def parse_elicitation_hook_output(
    result: HookOutsideReplResult, expected_event_name: str
) -> tuple[ElicitationResponse | None, HookBlockingError | None]:
    """
    `parseElicitationHookOutput`: exit code 2 blocks; otherwise a JSON document whose
    `hookSpecificOutput` names `expected_event_name` may answer with an `action` (a `decline`
    also blocks), and a top-level `decision: "block"` blocks. Anything else says nothing.
    """
    if result.blocked and not result.succeeded:
        return None, HookBlockingError(blocking_error=result.output or "Elicitation blocked by hook",
                                       command=result.command)
    trimmed = result.output.strip()
    if not trimmed.startswith("{"):
        return None, None
    try:
        parsed, _error = validate_hook_json(trimmed)
    except ValueError:
        return None, None
    if parsed is None or is_async_hook_json_output(parsed):
        return None, None
    if parsed.get("decision") == "block" or result.blocked:
        return None, HookBlockingError(blocking_error=parsed.get("reason") or "Elicitation blocked by hook",
                                       command=result.command)
    specific = parsed.get("hookSpecificOutput")
    if not isinstance(specific, Mapping) or specific.get("hookEventName") != expected_event_name:
        return None, None
    action = specific.get("action")
    if not action:
        return None, None
    response = ElicitationResponse(action=str(action), content=specific.get("content"))
    if action != "decline":
        return response, None
    default = "Elicitation denied by hook" if expected_event_name == "Elicitation" else (
        "Elicitation result blocked by hook")
    return response, HookBlockingError(blocking_error=parsed.get("reason") or default, command=result.command)


def _fold_elicitation_results(
    results: Sequence[HookOutsideReplResult], expected_event_name: str
) -> tuple[ElicitationResponse | None, HookBlockingError | None]:
    """The last response and the last blocking error over every hook's result, as upstream's loop."""
    response: ElicitationResponse | None = None
    blocking_error: HookBlockingError | None = None
    for result in results:
        parsed_response, parsed_blocking = parse_elicitation_hook_output(result, expected_event_name)
        if parsed_blocking is not None:
            blocking_error = parsed_blocking
        if parsed_response is not None:
            response = parsed_response
    return response, blocking_error


def _drop_none(fields: Mapping[str, Any]) -> dict[str, Any]:
    """`JSON.stringify` leaves `undefined` fields out of the hook input."""
    return {key: value for key, value in fields.items() if value is not None}


async def execute_elicitation_hooks(
    dispatcher: HookDispatcher | None,
    server_name: str,
    message: str,
    *,
    requested_schema: Mapping[str, Any] | None = None,
    permission_mode: str | None = None,
    signal: Any = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
    mode: str | None = None,
    url: str | None = None,
    elicitation_id: str | None = None,
) -> ElicitationHookResult:
    """`executeElicitationHooks`: hooks may answer an MCP server's elicitation, or block it."""
    if dispatcher is None:
        return ElicitationHookResult()
    hook_input = {
        **dispatcher.create_base_hook_input(permission_mode),
        "hook_event_name": HookEvent.ELICITATION.value,
        "mcp_server_name": server_name,
        "message": message,
        **_drop_none({"mode": mode, "url": url, "elicitation_id": elicitation_id,
                      "requested_schema": dict(requested_schema) if requested_schema is not None else None}),
    }
    results = await dispatcher.execute_hooks_outside_repl(
        hook_input, match_query=server_name, signal=signal, timeout_ms=timeout_ms
    )
    response, blocking_error = _fold_elicitation_results(results, HookEvent.ELICITATION.value)
    return ElicitationHookResult(elicitation_response=response, blocking_error=blocking_error)


async def execute_elicitation_result_hooks(
    dispatcher: HookDispatcher | None,
    server_name: str,
    action: str,
    *,
    content: Mapping[str, Any] | None = None,
    permission_mode: str | None = None,
    signal: Any = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
    mode: str | None = None,
    elicitation_id: str | None = None,
) -> ElicitationResultHookResult:
    """`executeElicitationResultHooks`: hooks see the user's answer and may override or block it."""
    if dispatcher is None:
        return ElicitationResultHookResult()
    hook_input = {
        **dispatcher.create_base_hook_input(permission_mode),
        "hook_event_name": HookEvent.ELICITATION_RESULT.value,
        "mcp_server_name": server_name,
        **_drop_none({"elicitation_id": elicitation_id, "mode": mode}),
        "action": action,
        **_drop_none({"content": dict(content) if content is not None else None}),
    }
    results = await dispatcher.execute_hooks_outside_repl(
        hook_input, match_query=server_name, signal=signal, timeout_ms=timeout_ms
    )
    response, blocking_error = _fold_elicitation_results(results, HookEvent.ELICITATION_RESULT.value)
    return ElicitationResultHookResult(elicitation_result_response=response, blocking_error=blocking_error)


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
    # Imported here: `app.tools.tools` imports the tool pipeline, which imports the hooks.
    from app.utils.tool_errors import validate_against_schema

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
class PluginHookContext:
    """`execCommandHook`'s `pluginRoot` / `pluginId`, and the run's settings for the plugin's options."""

    root: str
    plugin_id: str | None = None
    locations: Any = None


@dataclass(frozen=True, slots=True)
class MatchedHook:
    """`MatchedHook`: a hook, its plugin context (a plugin's hook), and where it came from."""

    hook: HookCommand
    hook_source: str = "settings"
    plugin_root: str | None = None
    plugin_id: str | None = None


def get_hooks_config(
    snapshot: HooksConfigSnapshot,
    hook_event: str,
    session_matchers: Mapping[str, Sequence[HookMatcher]] | None = None,
    registered_matchers: Mapping[str, Sequence[HookMatcher]] | None = None,
) -> list[HookMatcher]:
    """
    `getHooksConfig`: the snapshot's matchers, then the registered ones (an SDK host's callbacks
    and the plugins' hooks, `registerHookCallbacks`; a managed-only policy drops the plugins'),
    then session-scoped ones (an agent's or a skill's frontmatter hooks, function hooks), which a
    managed-only policy excludes.
    """
    matchers = list(snapshot.matchers_for(hook_event))
    if registered_matchers:
        # A managed-only policy skips the plugins' hooks (they carry `plugin_root`), not the SDK's.
        matchers.extend(m for m in registered_matchers.get(hook_event, ())
                        if not (snapshot.managed_only and m.plugin_root is not None))
    if not snapshot.managed_only and session_matchers:
        matchers.extend(session_matchers.get(hook_event, ()))
    return matchers


def _dedup_key(hook: HookCommand) -> tuple[str, ...]:
    condition = hook.if_condition or ""
    if isinstance(hook, CommandHook):
        return ("command", hook.shell or DEFAULT_HOOK_SHELL, hook.command, condition)
    if isinstance(hook, HttpHook):
        return ("http", hook.url, condition)
    if isinstance(hook, (CallbackHook, FunctionHook)):
        return (hook.type, str(id(hook)))
    return (hook.type, hook.prompt, condition)


def _dedup(hooks: list[MatchedHook]) -> list[MatchedHook]:
    """
    Group by type in upstream's order; within a type the last duplicate wins its slot. The key is
    namespaced by the plugin root (`hookDedupKey`), so two plugins' same template never collapse.
    """
    ordered: list[MatchedHook] = []
    for kind in ("command", "prompt", "agent", "http"):
        by_key: dict[tuple[str, ...], MatchedHook] = {}
        for matched in hooks:
            if matched.hook.type == kind:
                # A dict, like a JS Map, keeps the first position and takes the last value.
                by_key[(matched.plugin_root or "", *_dedup_key(matched.hook))] = matched
        ordered.extend(by_key.values())
    # Callback and function hooks are unique by nature: never deduplicated, in their order.
    ordered.extend(m for m in hooks if m.hook.type == "callback")
    ordered.extend(m for m in hooks if m.hook.type == "function")
    return ordered


def _hook_source(matcher: HookMatcher) -> str:
    """`getMatchingHooks`' `hookSource`: `plugin:<name>` for a plugin's hook, else `settings`."""
    if matcher.plugin_root is None:
        return "settings"
    return f"plugin:{matcher.plugin_name}" if matcher.plugin_name is not None else "plugin"


def _plugin_context(matched: MatchedHook) -> PluginHookContext | None:
    """A matched plugin hook's directory and id, for the command it runs."""
    if matched.plugin_root is None:
        return None
    return PluginHookContext(root=matched.plugin_root, plugin_id=matched.plugin_id)


def get_matching_hooks(
    snapshot: HooksConfigSnapshot,
    hook_event: str,
    hook_input: Mapping[str, Any],
    tools: Sequence[Any] | None = None,
    session_matchers: Mapping[str, Sequence[HookMatcher]] | None = None,
    registered_matchers: Mapping[str, Sequence[HookMatcher]] | None = None,
) -> list[MatchedHook]:
    """`getMatchingHooks`. Never raises: a failure here means no hooks, as upstream."""
    try:
        hook_matchers = get_hooks_config(snapshot, hook_event, session_matchers, registered_matchers)
        match_query = match_query_for(hook_input)
        if match_query:
            hook_matchers = [m for m in hook_matchers if not m.matcher or matches_pattern(match_query, m.matcher)]
        matched = [
            MatchedHook(hook=hook, hook_source=_hook_source(m), plugin_root=m.plugin_root, plugin_id=m.plugin_id)
            for m in hook_matchers for hook in m.hooks
        ]
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
    if isinstance(hook, (CallbackHook, FunctionHook)):
        return hook.type
    if hook.status_message:
        return hook.status_message
    if isinstance(hook, CommandHook):
        return hook.command
    if isinstance(hook, HttpHook):
        return hook.url
    return hook.prompt


PROJECT_DIR_ENV = "TDDAGENTS_PROJECT_DIR"


@dataclass(frozen=True, slots=True)
class CommandHookResult:
    """`execCommandHook`'s result."""

    stdout: str
    stderr: str
    output: str
    status: int
    aborted: bool = False
    backgrounded: bool = False


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0]


def hook_env(project_dir: str) -> dict[str, str]:
    """The child's environment: the parent's plus the project directory."""
    env = dict(os.environ)
    env[PROJECT_DIR_ENV] = project_dir
    return env


async def plugin_hook_command(hook: CommandHook, plugin: PluginHookContext) -> tuple[CommandHook, dict[str, str]]:
    """
    `execCommandHook`'s plugin half: `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}` and then
    `${user_config.X}` substituted in the command (plugin variables first, so a user value is
    opaque), and the child's `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA` and `CLAUDE_PLUGIN_OPTION_<KEY>`.
    A plugin directory that no longer exists raises (a missing script's exit 2 would read as a block).
    """
    from app.utils.plugins.plugin_directories import get_plugin_data_dir
    from app.utils.plugins.plugin_options_storage import (
        load_plugin_options,
        substitute_user_config_variables,
    )

    if not os.path.exists(plugin.root):
        suffix = f" ({plugin.plugin_id} — run /plugin to reinstall)" if plugin.plugin_id else ""
        raise RuntimeError(f"Plugin directory does not exist: {plugin.root}{suffix}")
    command = hook.command.replace("${CLAUDE_PLUGIN_ROOT}", plugin.root)
    env = {"CLAUDE_PLUGIN_ROOT": plugin.root}
    options: Mapping[str, Any] | None = None
    if plugin.plugin_id:
        data_dir = get_plugin_data_dir(plugin.plugin_id)
        command = command.replace("${CLAUDE_PLUGIN_DATA}", data_dir)
        env["CLAUDE_PLUGIN_DATA"] = data_dir
        if plugin.locations is not None:
            options = load_plugin_options(plugin.plugin_id, plugin.locations)
            command = substitute_user_config_variables(command, options)
    for key, value in (options or {}).items():
        env_key = re.sub(r"[^A-Za-z0-9_]", "_", key).upper()
        env[f"CLAUDE_PLUGIN_OPTION_{env_key}"] = _option_text(value)
    return replace(hook, command=command), env


def _option_text(value: Any) -> str:
    """`String(value)`: JS's rendering of a stored option."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(_option_text(v) for v in value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def safe_cwd(cwd: str, project_dir: str) -> str:
    """The working directory, or the project root when the working directory is missing."""
    return cwd if Path(cwd).is_dir() else project_dir


async def _spawn(hook: CommandHook, cwd: str, env: dict[str, str]) -> asyncio.subprocess.Process:
    shell_type = hook.shell or DEFAULT_HOOK_SHELL
    pipes: dict[str, Any] = {
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "cwd": cwd,
        "env": env,
        "start_new_session": True,
    }
    if shell_type == "powershell":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:
            raise RuntimeError(
                f"Hook \"{hook.command}\" has shell: 'powershell' but no PowerShell executable "
                "(pwsh or powershell) was found on PATH. Install PowerShell, or remove "
                "\"shell\": \"powershell\" to use bash."
            )
        return await asyncio.create_subprocess_exec(
            pwsh, "-NoProfile", "-NonInteractive", "-Command", hook.command, **pipes
        )
    return await asyncio.create_subprocess_shell(hook.command, **pipes)


async def exec_command_hook(
    hook: CommandHook,
    hook_event: str,
    hook_name: str,
    json_input: str,
    signal: Any,
    *,
    cwd: str,
    project_dir: str,
    registry: AsyncHookRegistry | None = None,
    hook_id: str | None = None,
    timeout_ms: int | None = None,
    force_sync: bool = False,
    request_prompt: Callable[[PromptRequest], Awaitable[PromptResponse]] | None = None,
    plugin: PluginHookContext | None = None,
) -> CommandHookResult:
    """
    `execCommandHook`. Failures to start or to talk to the child become a status-1 result
    with the error in stderr; they never raise, so one broken hook cannot fail its siblings.

    With `request_prompt` (the hook prompt protocol, `HOOK_PROMPTS`): stdin stays open, every
    complete stdout line that is a `PromptRequest` is answered in order through `request_prompt`
    and the answer written back to stdin as one JSON line, and those lines are left out of the
    hook's stdout. A failed answer closes stdin.
    """
    hook_id = hook_id or str(uuid.uuid4())
    if timeout_ms is None:
        timeout_ms = int(hook.timeout * 1000) if hook.timeout else TOOL_HOOK_EXECUTION_TIMEOUT_MS
    env = hook_env(project_dir)
    if plugin is not None:
        # Raises when the plugin directory is gone or a `${user_config.X}` is missing: the caller
        # turns it into a non-blocking error, as upstream's throw.
        hook, plugin_env = await plugin_hook_command(hook, plugin)
        env.update(plugin_env)
    try:
        process = await _spawn(hook, safe_cwd(cwd, project_dir), env)
    except Exception as exc:  # noqa: BLE001 - upstream reports any spawn error as status 1
        message = f"Error occurred while executing hook command: {exc}"
        return CommandHookResult(stdout="", stderr=message, output=message, status=1)
    capture = CapturedProcess(process)
    process_id = f"async_hook_{process.pid}"
    prompt_lines: set[str] = set()
    prompt_chain: list[asyncio.Future[None]] = []
    if request_prompt is not None:
        capture.line_listener = functools.partial(
            _on_prompt_line, capture=capture, request_prompt=request_prompt, seen=prompt_lines, chain=prompt_chain)

    if (hook.run_async or hook.async_rewake) and not force_sync and registry is not None:
        await capture.write_stdin(json_input + "\n")
        if hook.async_rewake:
            registry.register_rewake(hook_name=hook_name, capture=capture)
        else:
            registry.register_pending(
                process_id=process_id,
                hook_id=hook_id,
                async_response={"async": True, "asyncTimeout": timeout_ms},
                hook_name=hook_name,
                hook_event=hook_event,
                command=hook.command,
                capture=capture,
            )
        return CommandHookResult(stdout="", stderr="", output="", status=0, backgrounded=True)

    try:
        await capture.write_stdin(json_input + "\n", keep_open=request_prompt is not None)
    except (BrokenPipeError, ConnectionResetError):
        if request_prompt is not None:
            # With prompts, a stdin error is only logged: the hook may simply not read it.
            logger.debug("Hooks: stdin error during prompt flow")
        message = "Hook command closed stdin before hook input was fully written (EPIPE)"
        capture.kill()
        await capture.result()
        return CommandHookResult(stdout="", stderr=message, output=message, status=1)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    initial_checked = False
    while True:
        if capture.done.done():
            break
        if is_aborted(signal) or loop.time() >= deadline:
            capture.kill()
            await capture.result()
            return CommandHookResult(
                stdout=capture.stdout,
                stderr=capture.stderr,
                output=capture.output,
                status=capture.exit_code if capture.exit_code is not None else 1,
                aborted=True,
            )
        if not initial_checked:
            first = _first_line(capture.stdout).strip()
            if "}" in first:
                initial_checked = True
                if _is_async_line(first) and not force_sync and registry is not None:
                    registry.register_pending(
                        process_id=process_id,
                        hook_id=hook_id,
                        async_response=json.loads(first),
                        hook_name=hook_name,
                        hook_event=hook_event,
                        command=hook.command,
                        capture=capture,
                    )
                    return CommandHookResult(
                        stdout=capture.stdout, stderr=capture.stderr, output=capture.output, status=0
                    )
        capture.stdout_changed.clear()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                asyncio.shield(_either(capture)), timeout=min(ABORT_POLL_SECONDS, max(deadline - loop.time(), 0))
            )

    code = await capture.result()
    if prompt_chain:
        await prompt_chain[-1]
    stdout = capture.stdout
    if prompt_lines:
        stdout = "\n".join(line for line in stdout.split("\n") if line.strip() not in prompt_lines)
    return CommandHookResult(stdout=stdout, stderr=capture.stderr, output=capture.output, status=code)


def _on_prompt_line(
    line: str, *, capture: CapturedProcess, request_prompt: Callable[[PromptRequest], Awaitable[PromptResponse]],
    seen: set[str], chain: list[asyncio.Future[None]],
) -> None:
    """One stdout line: a `PromptRequest` is queued behind the earlier ones and answered on stdin."""
    trimmed = line.strip()
    if not trimmed:
        return
    try:
        request = parse_prompt_request(json.loads(trimmed))
    except ValueError:
        return
    if request is None:
        return
    seen.add(trimmed)
    previous = chain[-1] if chain else None

    async def answer() -> None:
        if previous is not None:
            await previous
        try:
            response = await request_prompt(request)
            stdin = capture.process.stdin
            if stdin is not None:
                stdin.write((json.dumps(response.as_dict(), separators=(",", ":")) + "\n").encode("utf-8"))
                await stdin.drain()
        except Exception as err:  # noqa: BLE001 - upstream: log, then destroy stdin
            logger.debug("Hooks: Prompt request handling failed: %s", err)
            capture.close_stdin()

    chain.append(asyncio.ensure_future(answer()))


async def _either(capture: CapturedProcess) -> None:
    """Wake on new stdout or on exit, whichever comes first."""
    changed: asyncio.Future[Any] = asyncio.ensure_future(capture.stdout_changed.wait())
    done: asyncio.Future[Any] = capture.done
    try:
        await asyncio.wait({changed, done}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        changed.cancel()


def _is_async_line(line: str) -> bool:
    try:
        parsed = json.loads(line)
    except ValueError:
        return False
    return isinstance(parsed, dict) and is_async_hook_json_output(parsed)


__all__ = [
    "PROJECT_DIR_ENV",
    "AgentHookExecutor",
    "CommandHookResult",
    "ElicitationHookResult",
    "ElicitationResponse",
    "ElicitationResultHookResult",
    "HookDispatcher",
    "HookOutsideReplResult",
    "HttpHookExecutor",
    "IfConditionMatcher",
    "MatchedHook",
    "PreCompactHookResult",
    "PromptHookExecutor",
    "dispatcher_of",
    "exec_command_hook",
    "execute_elicitation_hooks",
    "execute_elicitation_result_hooks",
    "execute_notification_hooks",
    "execute_permission_denied_hooks",
    "execute_permission_request_hooks",
    "execute_post_compact_hooks",
    "execute_post_tool_hooks",
    "execute_post_tool_use_failure_hooks",
    "execute_pre_compact_hooks",
    "execute_pre_tool_hooks",
    "execute_session_end_hooks",
    "execute_session_start_hooks",
    "execute_stop_failure_hooks",
    "execute_stop_hooks",
    "execute_subagent_start_hooks",
    "execute_user_prompt_submit_hooks",
    "get_hooks_config",
    "get_legacy_tool_names",
    "get_matching_hooks",
    "hook_display_text",
    "hook_env",
    "last_assistant_text",
    "matches_pattern",
    "parse_elicitation_hook_output",
    "prepare_if_condition_matcher",
    "safe_cwd",
]
