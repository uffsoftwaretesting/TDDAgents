"""
`SendMessage` (Phase 8), local agents only.

Ported from `reference/claude-code/src/tools/SendMessageTool/SendMessageTool.ts` ->
`SendMessageTool` (`validateInput`, `checkPermissions`, `isReadOnly`, `isEnabled`,
`mapToolResultToToolResultBlockParam`, and `call`'s local-agent branches) and `prompt.ts`.
A message to a **running** background agent is queued on its task (`queuePendingMessage`) and
drained at the worker's next tool round (`attachments/agent_messages.py`); a **stopped** agent,
or one with no task, is resumed in the background from its sidechain transcript
(`agents/resume.py`, `resumeAgentBackground`).

`isEnabled` is upstream's `isAgentSwarmsEnabled` for the external build: off unless
`TDDAGENTS_EXPERIMENTAL_AGENT_TEAMS` (upstream `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) is truthy;
the GrowthBook killswitch defaults on and has no counterpart here.

**TDD.** A resumed agent runs async, so a phase agent is never resumed (`agents/resume.py`).
Queuing a message to a running agent cannot reach a phase agent either: only agents without a
`phase` ever run as background tasks.

Not ported, as their gates are off or their subsystems absent: teammates and the mailbox
(`handleMessage`), broadcast (`"*"`), the structured shutdown/plan-approval protocol, the
agent-name registry, and the UDS/bridge peers. So `to` must be an agent id (upstream
`toAgentId`), which `validate_input` checks, and `message` is plain text only; the prompt keeps
upstream's text minus the rows and sections for those features.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.loop.agents.definition import AgentDefinition
from app.loop.agents.resume import resume_agent_background
from app.loop.config import RunConfig
from app.loop.context.tool_context import ToolContext
from app.loop.deps import LoopDeps
from app.loop.prompts.loader import parse_markdown_frontmatter
from app.loop.tasks.local_agent_task import LocalAgentTaskState, queue_pending_message
from app.loop.tools.base import BuiltTool, Tool, build_tool
from app.loop.tools.types import ToolResult, ValidationResult

SEND_MESSAGE_TOOL_NAME = "SendMessage"

#: Upstream's `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`.
AGENT_TEAMS_ENV = "TDDAGENTS_EXPERIMENTAL_AGENT_TEAMS"

#: `types/ids.ts` -> `AGENT_ID_PATTERN`: `a` + optional `<label>-` + 16 hex digits.
AGENT_ID_PATTERN = re.compile(r"^a(?:.+-)?[0-9a-f]{16}$")

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "tools" / "send_message.md"

SEND_MESSAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "to": {"type": "string", "description": "Recipient: an agent ID returned by the Agent tool"},
        "summary": {
            "type": "string",
            "description": "A 5-10 word summary shown as a preview in the UI (required when message is a string)",
        },
        "message": {"type": "string", "description": "Plain text message content"},
    },
    "required": ["to", "message"],
    "additionalProperties": False,
}


def is_agent_swarms_enabled() -> bool:
    """`isAgentSwarmsEnabled`, external build: the env opt-in, read when called."""
    return os.environ.get(AGENT_TEAMS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def to_agent_id(value: str) -> str | None:
    """`toAgentId`."""
    return value if AGENT_ID_PATTERN.match(value) else None


def _prompt() -> str:
    """`getPrompt()`, trimmed."""
    return parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1].strip()


def _invalid(message: str) -> ValidationResult:
    return ValidationResult(valid=False, message=message, error_code=9)


def build_send_message_tool(
    *,
    definitions: Mapping[str, AgentDefinition],
    worker_tools: Sequence[Tool],
    config: RunConfig,
    deps: LoopDeps,
    now: Callable[[], float] = time.time,
) -> BuiltTool:
    """The `SendMessage` tool; the resume arguments are the Agent tool's (`build_agent_tool`)."""

    def validate(args: dict[str, Any], context: ToolContext) -> ValidationResult:
        to = str(args.get("to", ""))
        if not to.strip():
            return _invalid("to must not be empty")
        if "@" in to:
            return _invalid('to must be a bare teammate name or "*" — there is only one team per session')
        summary = args.get("summary")
        if not summary or not str(summary).strip():
            return _invalid("summary is required when message is a string")
        if to_agent_id(to) is None:
            return _invalid("to must be an agent ID returned by the Agent tool: teammates are not available in "
                            "this build")
        return ValidationResult(valid=True)

    async def resume(agent_id: str, message: str, context: ToolContext) -> str:
        result = await resume_agent_background(
            agent_id=agent_id, prompt=message, context=context, definitions=definitions,
            worker_tools=worker_tools, config=config, deps=deps, now=now,
        )
        return result.output_file

    async def call(args: dict[str, Any], context: ToolContext) -> ToolResult:
        to = str(args["to"])
        message = str(args["message"])
        agent_id = to_agent_id(to)
        if agent_id is None:
            raise ValueError(f"Not an agent ID: {to}")
        task = context.get_app_state().tasks.get(agent_id)
        if isinstance(task, LocalAgentTaskState):
            if task.status == "running":
                queue_pending_message(agent_id, message, context.set_app_state)
                data = {"success": True, "message": f"Message queued for delivery to {to} at its next tool round."}
            else:
                try:
                    output = await resume(agent_id, message, context)
                    data = {"success": True, "message": (
                        f'Agent "{to}" was stopped ({task.status}); resumed it in the background with your '
                        f"message. You'll be notified when it finishes. Output: {output}")}
                except Exception as exc:  # noqa: BLE001 - upstream's catch reports any failure
                    data = {"success": False,
                            "message": f'Agent "{to}" is stopped ({task.status}) and could not be resumed: {exc}'}
        else:
            try:
                output = await resume(agent_id, message, context)
                data = {"success": True, "message": (
                    f'Agent "{to}" had no active task; resumed from transcript in the background with your '
                    f"message. You'll be notified when it finishes. Output: {output}")}
            except Exception as exc:  # noqa: BLE001 - upstream's catch reports any failure
                data = {"success": False, "message": (
                    f'Agent "{to}" is registered but has no transcript to resume. It may have been cleaned up. '
                    f"({exc})")}
        # `mapToolResultToToolResultBlockParam`: `jsonStringify(data)`.
        return ToolResult(content=json.dumps(data, separators=(",", ":"), ensure_ascii=False))

    return build_tool(
        name=SEND_MESSAGE_TOOL_NAME,
        prompt=_prompt(),
        call=call,
        input_schema=SEND_MESSAGE_SCHEMA,
        description=lambda args: "Send a message to another agent",
        is_enabled=is_agent_swarms_enabled,
        is_read_only=lambda args: isinstance(args.get("message"), str),
        validate_input=validate,
        max_result_size_chars=100_000,
    )


__all__ = [
    "AGENT_ID_PATTERN",
    "AGENT_TEAMS_ENV",
    "SEND_MESSAGE_SCHEMA",
    "SEND_MESSAGE_TOOL_NAME",
    "build_send_message_tool",
    "is_agent_swarms_enabled",
    "to_agent_id",
]
