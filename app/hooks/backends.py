"""
Execution backends for Command, Prompt, Agent, and HTTP hooks (Parts H3, H4).

Ported from:
- `reference/claude-code/src/utils/hooks.ts` -> shell spawn, exit-code contract, JSON stdout
- `reference/claude-code/src/utils/hooks/execPromptHook.ts` -> LLM evaluation, schema parsing
- `reference/claude-code/src/utils/hooks/execAgentHook.ts` -> agentic verification
- `reference/claude-code/src/utils/hooks/execHttpHook.ts` -> webhook execution, env interpolation
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.hooks.schemas import AgentHook, CommandHook, HttpHook, PromptHook

logger = logging.getLogger("TDDOrchestrator.Hooks")

BLOCKING_EXIT_CODE = 2

ModelCaller = Callable[[str, str], Awaitable[str] | str]
AgentRunner = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]
HttpClient = Callable[[str, dict[str, Any], dict[str, str]], tuple[int, str]]


@dataclass
class HookExecutionOutcome:
    """The normalized verdict and output of running a single hook."""

    denied: bool = False
    reason: str = ""
    updated_input: dict[str, Any] | None = None
    additional_context: str = ""
    stop_continuation: bool = False
    prevent_continuation: bool = False
    blocking_errors: tuple[str, ...] = ()
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


def interpolate_arguments(template: str, payload: dict[str, Any]) -> str:
    """Replaces $ARGUMENTS in template with JSON stringified payload."""
    payload_json = json.dumps(payload, indent=2)
    return template.replace("$ARGUMENTS", payload_json)


def interpolate_headers(
    headers: dict[str, str],
    allowed_env_vars: tuple[str, ...],
) -> dict[str, str]:
    """
    Interpolates environment variables in HTTP header values.

    Only variables listed in `allowed_env_vars` are resolved; all other
    $VAR or ${VAR} references are replaced with empty strings.
    """
    allowed_set = set(allowed_env_vars)

    def _replace_var(match: re.Match[str]) -> str:
        var_name = match.group(1) or match.group(2)
        if var_name in allowed_set:
            return os.environ.get(var_name, "")
        return ""

    pattern = re.compile(r"\$\{([A-Za-z0-9_]+)\}|\$([A-Za-z0-9_]+)")
    resolved: dict[str, str] = {}
    for key, value in headers.items():
        resolved[key] = pattern.sub(_replace_var, value)
    return resolved


class CommandHookBackend:
    """Runs a shell command hook on the host (outside sandbox boundary)."""

    def __init__(self, project_root: Path | str = ".") -> None:
        self.project_root = Path(project_root)

    def execute(
        self,
        hook: CommandHook,
        payload: dict[str, Any],
        event: str,
    ) -> HookExecutionOutcome:
        if hook.status_message:
            logger.info("🪝 %s", hook.status_message)

        shell_cmd = ["/bin/bash", "-c", hook.command] if hook.shell == "bash" else [hook.shell, "-c", hook.command]

        try:
            completed = subprocess.run(
                shell_cmd,
                input=json.dumps(payload),
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=hook.timeout,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Hook '%s' exceeded %ss and was skipped.", hook.label, hook.timeout)
            return HookExecutionOutcome(exit_code=124, stderr="Hook timed out")
        except OSError as exc:
            logger.warning("Hook '%s' could not be started: %s", hook.label, exc)
            return HookExecutionOutcome(exit_code=127, stderr=str(exc))

        return self.interpret(
            hook,
            completed.returncode,
            completed.stdout,
            completed.stderr,
            event,
        )

    def interpret(
        self,
        hook: CommandHook,
        exit_code: int,
        stdout: str,
        stderr: str,
        event: str,
    ) -> HookExecutionOutcome:
        outcome = HookExecutionOutcome(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

        if exit_code == BLOCKING_EXIT_CODE:
            reason = (stderr or stdout).strip() or f"Blocked by hook '{hook.label}'."
            if event == "PreToolUse":
                outcome.denied = True
                outcome.reason = reason
            elif event == "PostToolUse":
                outcome.additional_context = reason
                outcome.stop_continuation = True
            elif event == "Stop":
                outcome.blocking_errors = (reason,)
            else:
                outcome.denied = True
                outcome.reason = reason
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
        self,
        outcome: HookExecutionOutcome,
        stdout: str,
        event: str,
        hook: CommandHook,
    ) -> None:
        text = stdout.strip()
        if not text:
            return

        document = None
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            for line in reversed(text.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        document = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue

        if not isinstance(document, dict):
            logger.debug("Hook '%s' produced non-JSON stdout; treated as a log line.", hook.label)
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
        if decision == "deny" and event == "PreToolUse":
            outcome.denied = True
            outcome.reason = reason if isinstance(reason, str) and reason else f"Denied by hook '{hook.label}'."
        elif decision == "allow":
            outcome.denied = False
            outcome.reason = ""
        elif decision is not None:
            logger.warning(
                "Hook '%s' returned unsupported permissionDecision '%s'; ignoring it.",
                hook.label,
                decision,
            )

        if event == "PreToolUse":
            updated = document.get("updatedInput")
            if isinstance(updated, dict):
                outcome.updated_input = updated
        elif document.get("continue") is False:
            outcome.stop_continuation = True

        if document.get("preventContinuation") is True:
            outcome.prevent_continuation = True

        # Structured {"ok": false, "reason": "..."} support
        if "ok" in document:
            ok_val = bool(document["ok"])
            if not ok_val:
                r = str(document.get("reason") or f"Hook '{hook.label}' condition not met.")
                outcome.exit_code = BLOCKING_EXIT_CODE
                if event == "PreToolUse":
                    outcome.denied = True
                    outcome.reason = r
                elif event == "Stop":
                    outcome.blocking_errors = (r,)
                else:
                    outcome.additional_context = r
                    outcome.stop_continuation = True


class PromptHookBackend:
    """Evaluates a prompt hook with an LLM."""

    SYSTEM_PROMPT = (
        "You are evaluating a hook in Claude Code.\n\n"
        "Your response must be a JSON object matching one of the following schemas:\n"
        '1. If the condition is met, return: {"ok": true}\n'
        '2. If the condition is not met, return: {"ok": false, "reason": "Reason for why it is not met"}'
    )

    def __init__(self, model_caller: ModelCaller | None = None) -> None:
        self.model_caller = model_caller

    async def execute(
        self,
        hook: PromptHook,
        payload: dict[str, Any],
        event: str,
    ) -> HookExecutionOutcome:
        processed_prompt = interpolate_arguments(hook.prompt, payload)
        if self.model_caller is None:
            # Deterministic default: condition met if no model caller configured
            return HookExecutionOutcome(exit_code=0, stdout='{"ok": true}')

        try:
            res = self.model_caller(self.SYSTEM_PROMPT, processed_prompt)
            if hasattr(res, "__await__"):
                raw_response = await res
            else:
                raw_response = res
        except Exception as exc:
            logger.warning("Prompt hook '%s' model call failed: %s", hook.label, exc)
            return HookExecutionOutcome(exit_code=1, stderr=str(exc))

        return self.interpret(hook, str(raw_response), event)

    def interpret(self, hook: PromptHook, raw_response: str, event: str) -> HookExecutionOutcome:
        text = raw_response.strip()
        outcome = HookExecutionOutcome(stdout=text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Malformed JSON from model is a non-blocking error (H4)
            logger.warning("Prompt hook '%s' returned invalid JSON: %s", hook.label, text[:200])
            outcome.exit_code = 1
            outcome.stderr = "JSON validation failed"
            return outcome

        if not isinstance(data, dict) or "ok" not in data:
            outcome.exit_code = 1
            outcome.stderr = "Schema validation failed"
            return outcome

        if not data["ok"]:
            reason = str(data.get("reason") or f"Condition not met by hook '{hook.label}'")
            outcome.exit_code = BLOCKING_EXIT_CODE
            if event == "PreToolUse":
                outcome.denied = True
                outcome.reason = reason
            elif event == "PostToolUse":
                outcome.additional_context = reason
                outcome.stop_continuation = True
            elif event == "Stop":
                outcome.blocking_errors = (reason,)
            else:
                outcome.denied = True
                outcome.reason = reason
        else:
            outcome.exit_code = 0

        return outcome


class AgentHookBackend:
    """Evaluates an agentic verification hook with inspection tools."""

    def __init__(self, agent_runner: AgentRunner | None = None) -> None:
        self.agent_runner = agent_runner

    async def execute(
        self,
        hook: AgentHook,
        payload: dict[str, Any],
        event: str,
    ) -> HookExecutionOutcome:
        processed_prompt = interpolate_arguments(hook.prompt, payload)
        if self.agent_runner is None:
            return HookExecutionOutcome(exit_code=0, stdout='{"ok": true}')

        try:
            res = self.agent_runner(processed_prompt, payload)
            if hasattr(res, "__await__"):
                data = await res
            else:
                data = res
        except Exception as exc:
            logger.warning("Agent hook '%s' execution failed: %s", hook.label, exc)
            return HookExecutionOutcome(exit_code=1, stderr=str(exc))

        outcome = HookExecutionOutcome(stdout=json.dumps(data))
        if not isinstance(data, dict):
            outcome.exit_code = 1
            outcome.stderr = "Invalid agent output format"
            return outcome

        if not data.get("ok", True):
            reason = str(data.get("reason") or f"Agent verification failed for '{hook.label}'")
            outcome.exit_code = BLOCKING_EXIT_CODE
            if event == "PreToolUse":
                outcome.denied = True
                outcome.reason = reason
            elif event == "PostToolUse":
                outcome.additional_context = reason
                outcome.stop_continuation = True
            elif event == "Stop":
                outcome.blocking_errors = (reason,)
            else:
                outcome.denied = True
                outcome.reason = reason
        else:
            outcome.exit_code = 0

        return outcome


class HttpHookBackend:
    """Posts hook input JSON to a remote webhook endpoint."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self.http_client = http_client

    def execute(
        self,
        hook: HttpHook,
        payload: dict[str, Any],
        event: str,
    ) -> HookExecutionOutcome:
        headers = interpolate_headers(dict(hook.headers), hook.allowed_env_vars)
        headers.setdefault("Content-Type", "application/json")

        if self.http_client is not None:
            status_code, body = self.http_client(hook.url, payload, headers)
        else:
            status_code, body = self._default_http_post(hook.url, payload, headers, hook.timeout)

        outcome = HookExecutionOutcome(stdout=body)
        if status_code == 200:
            outcome.exit_code = 0
            # Optional JSON response inspection
            if body.strip():
                try:
                    data = json.loads(body)
                    if isinstance(data, dict) and data.get("ok") is False:
                        outcome.exit_code = BLOCKING_EXIT_CODE
                        reason = str(data.get("reason") or f"HTTP hook rejected by {hook.url}")
                        outcome.reason = reason
                        if event == "PreToolUse":
                            outcome.denied = True
                        elif event == "Stop":
                            outcome.blocking_errors = (reason,)
                except json.JSONDecodeError:
                    pass
        elif status_code == BLOCKING_EXIT_CODE or status_code == 403 or status_code == 422:
            outcome.exit_code = BLOCKING_EXIT_CODE
            outcome.denied = True
            outcome.reason = body.strip() or f"HTTP hook blocked by {hook.url}"
            if event == "Stop":
                outcome.blocking_errors = (outcome.reason,)
        else:
            logger.warning("HTTP hook '%s' returned status %d; ignored.", hook.label, status_code)
            outcome.exit_code = 1
            outcome.stderr = f"HTTP {status_code}: {body}"

        return outcome

    @staticmethod
    def _default_http_post(
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                return response.status, body
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            return exc.code, err_body
        except Exception as exc:
            return 500, str(exc)
