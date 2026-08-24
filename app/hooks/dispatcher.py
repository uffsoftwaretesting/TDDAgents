"""
The hook dispatcher: shell commands as tool middleware.

A hook is an ordinary executable. The dispatcher writes a JSON description of the tool
event to its stdin, waits, and reads its verdict from the process exit code — with an
optional typed JSON document on stdout for finer control. Nothing about the contract is
Python-specific, which is the point: a hook can be a bash one-liner, a Python script, or a
compiled binary, and none of them need to import anything from this repository.

    exit 0      proceed
    exit 2      PreToolUse  -> veto the call; stderr becomes the reason the model reads
                PostToolUse -> the tool already ran, so its output stands as ground truth
                               and stderr rides along as feedback; the step is marked
                               `stop_continuation` for the agent runtime to halt on
    otherwise   logged and ignored — a broken hook must not break the pipeline

On stdout, a JSON object is honored when it parses and ignored as plain text when it does
not, so a hook that merely echoes a log line still works:

    {"permissionDecision": "allow" | "deny",
     "permissionDecisionReason": "...",
     "updatedInput": { ... },        # PreToolUse only
     "additionalContext": "...",
     "continue": false}              # PostToolUse only

`ask` is deliberately absent from `permissionDecision`: TDDAgents has no interactive
prompt to route it to, and silently treating it as `allow` or `deny` would be worse than
rejecting it outright.

**Hooks run on the host, outside the workspace boundary.** A hook is host-side
configuration, exactly like the shell that launched the pipeline; the `workspace:` field
governs agents, not the operator's own middleware. The merged configuration is recorded at
construction so a run's event log can say which hooks were live.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.hooks.config import HookCommand, HookSettings, load_hook_settings
from app.tools.rules import rule_matches

logger = logging.getLogger("TDDOrchestrator.Hooks")

BLOCKING_EXIT_CODE = 2


class HookEvent(StrEnum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"


@dataclass
class HookOutcome:
    """The merged verdict of every hook that ran for one event."""

    denied: bool = False
    reason: str = ""
    updated_input: dict[str, Any] | None = None
    additional_context: str = ""
    stop_continuation: bool = False


def _matches(matcher: str | None, tool_name: str) -> bool:
    """
    Tool-name matching. `None` and `*` match everything; otherwise it is an exact match or
    a regex-free alternation (`Bash|Grep`), which covers every real configuration without
    making a settings typo into a catastrophic wildcard.
    """
    if matcher is None or matcher == "*":
        return True
    return tool_name in {part.strip() for part in matcher.split("|") if part.strip()}


class HookDispatcher:
    """Runs the configured shell hooks around a tool call."""

    def __init__(self, settings: HookSettings, project_root: Path | str = ".") -> None:
        self.settings = settings
        self.project_root = Path(project_root)
        logger.info("🪝 Hook configuration: %s", settings.describe())

    @classmethod
    def from_project(cls, project_root: Path | str, home: Path | None = None) -> "HookDispatcher":
        return cls(load_hook_settings(project_root, home), project_root)

    def run(
        self,
        event: HookEvent,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        ctx_fields: dict[str, Any] | None = None,
        tool_response: str | None = None,
        command: str | None = None,
    ) -> HookOutcome:
        """
        Runs every hook configured for `event` that matches this call.

        The first denial short-circuits: once a PreToolUse hook has vetoed, running the
        rest would only spawn processes whose verdicts cannot change the outcome.
        """
        outcome = HookOutcome()
        contexts: list[str] = []

        for hook in self._applicable(event, tool_name, command):
            payload = self._payload(event, tool_name, tool_input, ctx_fields, tool_response)
            single = self._run_one(hook, payload, event)

            if single.additional_context:
                contexts.append(single.additional_context)
            if single.updated_input is not None:
                outcome.updated_input = single.updated_input
            if single.stop_continuation:
                outcome.stop_continuation = True
            if single.denied:
                outcome.denied = True
                outcome.reason = single.reason
                break

        outcome.additional_context = "\n".join(contexts)
        return outcome

    # ── Internals ────────────────────────────────────────────────────────────

    def _applicable(
        self, event: HookEvent, tool_name: str, command: str | None
    ) -> list[HookCommand]:
        """
        Narrows the configured hooks to the ones worth spawning a process for.

        `if:` is evaluated here rather than inside the hook, which is the whole reason it
        exists — `Bash(pip *)` should cost nothing on a call that is not a pip install.
        """
        applicable: list[HookCommand] = []
        for matcher in self.settings.matchers_for(str(event)):
            if not _matches(matcher.matcher, tool_name):
                continue
            for hook in matcher.hooks:
                if hook.if_condition and not rule_matches(hook.if_condition, tool_name, command):
                    continue
                applicable.append(hook)
        return applicable

    def _payload(
        self,
        event: HookEvent,
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

    def _run_one(
        self, hook: HookCommand, payload: dict[str, Any], event: HookEvent
    ) -> HookOutcome:
        if hook.status_message:
            logger.info("🪝 %s", hook.status_message)

        try:
            completed = subprocess.run(
                ["/bin/bash", "-c", hook.command],
                input=json.dumps(payload),
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=hook.timeout,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "Hook '%s' exceeded %ss and was skipped.", hook.label, hook.timeout
            )
            return HookOutcome()
        except OSError as exc:
            logger.warning("Hook '%s' could not be started: %s", hook.label, exc)
            return HookOutcome()

        return self._interpret(hook, completed.returncode, completed.stdout, completed.stderr, event)

    def _interpret(
        self, hook: HookCommand, exit_code: int, stdout: str, stderr: str, event: HookEvent
    ) -> HookOutcome:
        outcome = HookOutcome()

        # Exit codes are the baseline contract; JSON on stdout refines it.
        if exit_code == BLOCKING_EXIT_CODE:
            reason = (stderr or stdout).strip() or f"Blocked by hook '{hook.label}'."
            if event is HookEvent.PRE_TOOL_USE:
                outcome.denied = True
                outcome.reason = reason
            else:
                # The side effect already landed. Flag it; never pretend it did not happen.
                outcome.additional_context = reason
                outcome.stop_continuation = True
        elif exit_code != 0:
            logger.warning(
                "Hook '%s' exited %d (non-blocking); output: %s",
                hook.label,
                exit_code,
                (stderr or stdout).strip()[:500],
            )
            return outcome

        self._apply_json(outcome, stdout, event, hook)
        return outcome

    def _apply_json(
        self, outcome: HookOutcome, stdout: str, event: HookEvent, hook: HookCommand
    ) -> None:
        """Overlays the typed stdout document, if there is one. Plain text is fine."""
        text = stdout.strip()
        if not text:
            return

        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Hook '%s' produced non-JSON stdout; treated as a log line.", hook.label)
            return

        if not isinstance(document, dict):
            return

        context = document.get("additionalContext")
        if isinstance(context, str) and context:
            outcome.additional_context = (
                f"{outcome.additional_context}\n{context}".strip()
                if outcome.additional_context
                else context
            )

        decision = document.get("permissionDecision")
        reason = document.get("permissionDecisionReason")
        if decision == "deny" and event is HookEvent.PRE_TOOL_USE:
            outcome.denied = True
            outcome.reason = (
                reason if isinstance(reason, str) and reason else f"Denied by hook '{hook.label}'."
            )
        elif decision == "allow":
            outcome.denied = False
            outcome.reason = ""
        elif decision is not None:
            logger.warning(
                "Hook '%s' returned an unsupported permissionDecision '%s'; ignoring it.",
                hook.label,
                decision,
            )

        if event is HookEvent.PRE_TOOL_USE:
            updated = document.get("updatedInput")
            if isinstance(updated, dict):
                outcome.updated_input = updated
        elif document.get("continue") is False:
            outcome.stop_continuation = True
