"""
The SDK stream-json wire shapes of TDDAgents' messages.

Ported from:
- `reference/claude-code/src/utils/queryHelpers.ts` -> `normalizeMessage` (assistant, user and
  agent-progress messages as `SDKMessage`s),
- `reference/claude-code/src/utils/messages/systemInit.ts` -> `sdkCompatToolName`,
  `buildSystemInitMessage`,
- `reference/claude-code/src/QueryEngine.ts` -> the `result`, `compact_boundary`, `api_retry`,
  `tool_use_summary` and `stream_event` messages it yields,
- `reference/claude-code/src/services/api/claude.ts`'s raw stream events, synthesized from the
  provider's chunks (`StreamEventSynthesizer`, user decision 2026-11-01: any provider's stream
  reads as Anthropic's `message_start` / `content_block_*` / `message_delta` / `message_stop`).

The conversation is LangChain's; the wire is Anthropic's SDK JSON whatever the provider (user
decision 2026-11-01): one content block per assistant message (as upstream's stream), a tool
result as a `user` message carrying `tool_result` and `tool_use_result`. TDDAgents adds the
loop's `terminal_reason` to the result and its own `system` subtypes (`tdd_ledger`, `tdd_plan`,
`tdd_transition`), which SDK clients ignore (user decision 2026-11-01).

Lives at `app/utils/sdk_messages.py` rather than `app/utils/messages/system_init.py`, because
`app/utils/messages.py` is a module; the upstream paths are named here instead.
"""

from __future__ import annotations

import uuid as uuid_module
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)

from app.constants.product import VERSION

#: `AGENT_TOOL_NAME` / `LEGACY_AGENT_TOOL_NAME`: the wire still says `Task` (`sdkCompatToolName`).
AGENT_TOOL_NAME = "Agent"
LEGACY_AGENT_TOOL_NAME = "Task"
#: `DEFAULT_OUTPUT_STYLE_NAME`.
DEFAULT_OUTPUT_STYLE_NAME = "default"


def _uuid() -> str:
    return str(uuid_module.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sdk_compat_tool_name(name: str) -> str:
    """`sdkCompatToolName`: `Agent` is still `Task` on the wire."""
    return LEGACY_AGENT_TOOL_NAME if name == AGENT_TOOL_NAME else name


def sdk_usage(message: Any) -> dict[str, int]:
    """Anthropic's `usage` from LangChain's `usage_metadata` (cache reads and writes split out)."""
    usage = dict(getattr(message, "usage_metadata", None) or {})
    details = dict(usage.get("input_token_details") or {})
    return {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "cache_creation_input_tokens": int(details.get("cache_creation", 0) or 0),
        "cache_read_input_tokens": int(details.get("cache_read", 0) or 0),
    }


def _model_of(message: Any) -> str:
    meta = getattr(message, "response_metadata", None) or {}
    return str(meta.get("model") or meta.get("model_name") or "")


def assistant_content_blocks(message: AIMessage) -> list[dict[str, Any]]:
    """The message's Anthropic content blocks: text, thinking, then its tool calls as `tool_use`."""
    blocks: list[dict[str, Any]] = []
    content = message.content
    if isinstance(content, str):
        if content:
            blocks.append({"type": "text", "text": content})
    else:
        for block in content:
            if isinstance(block, str):
                if block:
                    blocks.append({"type": "text", "text": block})
            elif isinstance(block, dict) and block.get("type") in ("text", "thinking", "redacted_thinking"):
                blocks.append({k: v for k, v in block.items() if k != "index"})
    for call in message.tool_calls:
        blocks.append({"type": "tool_use", "id": str(call.get("id") or ""), "name": str(call.get("name") or ""),
                       "input": dict(call.get("args") or {})})
    return blocks


def sdk_assistant_message(
    message: AIMessage, session_id: str, *, parent_tool_use_id: str | None = None,
    stop_reason: str | None = None,
) -> dict[str, Any] | None:
    """`normalizeMessage`'s assistant case; `None` for an empty message (`isNotEmptyMessage`)."""
    blocks = assistant_content_blocks(message)
    if not blocks or all(b.get("type") == "text" and not str(b.get("text", "")).strip() for b in blocks):
        return None
    sdk: dict[str, Any] = {
        "type": "assistant",
        "message": {
            "id": message.id or f"msg_{uuid_module.uuid4().hex}",
            "type": "message",
            "role": "assistant",
            "model": _model_of(message),
            "content": blocks,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": sdk_usage(message),
        },
        "parent_tool_use_id": parent_tool_use_id,
        "session_id": session_id,
        "uuid": _uuid(),
    }
    if message.additional_kwargs.get("is_api_error_message"):
        sdk["error"] = "unknown"
    return sdk


def user_message_content(message: BaseMessage) -> str | list[dict[str, Any]]:
    """A user message's content: its text, or its blocks (images as Anthropic image blocks)."""
    content = message.content
    if isinstance(content, str):
        return content
    blocks: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, str):
            blocks.append({"type": "text", "text": block})
        elif isinstance(block, dict) and block.get("type") == "image" and "base64" in block:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": block.get("mime_type"),
                                                       "data": block["base64"]}})
        elif isinstance(block, dict):
            blocks.append(dict(block))
    return blocks


def sdk_user_message(
    message: HumanMessage | ToolMessage, session_id: str, *, parent_tool_use_id: str | None = None,
    is_replay: bool = False,
) -> dict[str, Any]:
    """`normalizeMessage`'s user case: a prompt, or a tool result with its `tool_use_result`."""
    sdk: dict[str, Any] = {"type": "user", "parent_tool_use_id": parent_tool_use_id, "session_id": session_id,
                           "uuid": _uuid(), "timestamp": _now()}
    if isinstance(message, ToolMessage):
        result: dict[str, Any] = {"type": "tool_result", "tool_use_id": message.tool_call_id,
                                  "content": user_message_content(message)}
        if message.status == "error":
            result["is_error"] = True
        sdk["message"] = {"role": "user", "content": [result]}
        if "tool_use_result" in message.additional_kwargs:
            sdk["tool_use_result"] = message.additional_kwargs["tool_use_result"]
    else:
        sdk["message"] = {"role": "user", "content": user_message_content(message)}
        if message.additional_kwargs.get("is_meta"):
            sdk["isSynthetic"] = True
    if is_replay:
        sdk["isReplay"] = True
    return sdk


@dataclass(frozen=True, slots=True)
class SystemInitInputs:
    """`SystemInitInputs`."""

    cwd: str
    tools: Sequence[str]
    mcp_servers: Sequence[tuple[str, str]]
    model: str
    permission_mode: str
    commands: Sequence[str]
    agents: Sequence[str]
    skills: Sequence[str]
    plugins: Sequence[Mapping[str, str]] = ()
    api_key_source: str = "none"
    output_style: str = DEFAULT_OUTPUT_STYLE_NAME


def build_system_init_message(inputs: SystemInitInputs, session_id: str) -> dict[str, Any]:
    """`buildSystemInitMessage`: the session's metadata, first on the stream of every turn."""
    return {
        "type": "system",
        "subtype": "init",
        "cwd": inputs.cwd,
        "session_id": session_id,
        "tools": [sdk_compat_tool_name(t) for t in inputs.tools],
        "mcp_servers": [{"name": name, "status": status} for name, status in inputs.mcp_servers],
        "model": inputs.model,
        "permissionMode": inputs.permission_mode,
        "slash_commands": list(inputs.commands),
        "apiKeySource": inputs.api_key_source,
        "betas": [],
        "claude_code_version": VERSION,
        "output_style": inputs.output_style,
        "agents": list(inputs.agents),
        "skills": list(inputs.skills),
        "plugins": [dict(p) for p in inputs.plugins],
        "uuid": _uuid(),
        "fast_mode_state": "off",
    }


def sdk_result_message(result: Any, session_id: str) -> dict[str, Any]:
    """The `result` message from the engine's `ResultMessage` (`terminal_reason` is TDDAgents')."""
    sdk: dict[str, Any] = {
        "type": "result",
        "subtype": result.subtype,
        "is_error": result.is_error,
        "duration_ms": result.duration_ms,
        "duration_api_ms": result.duration_api_ms,
        "num_turns": result.num_turns,
        "stop_reason": result.stop_reason,
        "session_id": session_id,
        "total_cost_usd": result.total_cost_usd,
        "usage": dict(result.usage),
        "modelUsage": dict(result.model_usage),
        "permission_denials": [
            {**d, "tool_name": sdk_compat_tool_name(str(d.get("tool_name")))} for d in result.permission_denials],
        "uuid": result.uuid,
        "terminal_reason": result.terminal_reason,
    }
    if result.subtype == "success":
        sdk["result"] = result.result or ""
    if result.errors:
        sdk["errors"] = list(result.errors)
    if getattr(result, "structured_output", None) is not None:
        sdk["structured_output"] = result.structured_output
    return sdk


def sdk_tool_use_summary(summary: Any, session_id: str) -> dict[str, Any]:
    """QueryEngine's `tool_use_summary` message."""
    return {"type": "tool_use_summary", "summary": summary.summary,
            "preceding_tool_use_ids": list(summary.preceding_tool_use_ids), "session_id": session_id,
            "uuid": summary.uuid}


def sdk_compact_boundary(trigger: str, pre_tokens: int, session_id: str) -> dict[str, Any]:
    """QueryEngine's `compact_boundary` message (`toSDKCompactMetadata`)."""
    return {"type": "system", "subtype": "compact_boundary", "session_id": session_id, "uuid": _uuid(),
            "compact_metadata": {"trigger": trigger, "pre_tokens": pre_tokens}}


def sdk_api_retry(attempt: int, max_retries: int, retry_delay_ms: int, error_status: int | None, error: str,
                  session_id: str) -> dict[str, Any]:
    """QueryEngine's `api_retry` message."""
    return {"type": "system", "subtype": "api_retry", "attempt": attempt, "max_retries": max_retries,
            "retry_delay_ms": retry_delay_ms, "error_status": error_status, "error": error,
            "session_id": session_id, "uuid": _uuid()}


def sdk_status(session_id: str, *, status: str | None = None, permission_mode: str | None = None) -> dict[str, Any]:
    """`system/status`: `compacting` or `null`, and the permission mode when it changed."""
    message: dict[str, Any] = {"type": "system", "subtype": "status", "status": status, "session_id": session_id,
                               "uuid": _uuid()}
    if permission_mode is not None:
        message["permissionMode"] = permission_mode
    return message


def sdk_tdd_message(subtype: str, data: Mapping[str, Any], session_id: str) -> dict[str, Any]:
    """TDDAgents' own `system` messages: `tdd_ledger`, `tdd_plan`, `tdd_transition`."""
    return {"type": "system", "subtype": subtype, **dict(data), "session_id": session_id, "uuid": _uuid()}


def sdk_messages_for_event(
    event: Any, session_id: str, *, replay_user_messages: bool = False,
) -> list[dict[str, Any]]:
    """
    `QueryEngine.submitMessage`'s output switch over one loop event, as SDK messages:
    assistant and user messages (`normalizeMessage`; attachments are not yielded, except a
    queued command's replay with `replay_user_messages`), the compact boundary, the tool-use
    summary and the result. Other system messages and the loop's `Terminated` yield nothing.
    """
    from app.services.compact.compact import BOUNDARY_KEY, is_compact_boundary_message
    from app.utils.attachments import attachment_of
    from app.utils.messages import ToolUseSummaryMessage

    if getattr(event, "type", None) == "result" and hasattr(event, "terminal_reason"):
        return [sdk_result_message(event, session_id)]
    if isinstance(event, ToolUseSummaryMessage):
        return [sdk_tool_use_summary(event, session_id)]
    if not isinstance(event, BaseMessage):
        return []
    if is_compact_boundary_message(event):
        meta = event.additional_kwargs[BOUNDARY_KEY]
        return [sdk_compact_boundary(str(meta.get("trigger")), int(meta.get("preTokens") or 0), session_id)]
    if isinstance(event, AIMessage):
        from app.query_engine import normalize_stop_reason

        message = sdk_assistant_message(event, session_id, stop_reason=normalize_stop_reason(event))
        return [message] if message is not None else []
    attachment = attachment_of(event)
    if attachment is not None:
        if replay_user_messages and attachment.get("type") == "queued_command":
            return [{"type": "user", "message": {"role": "user", "content": attachment.get("prompt")},
                     "session_id": session_id, "parent_tool_use_id": None,
                     "uuid": str(attachment.get("source_uuid") or _uuid()), "timestamp": _now(), "isReplay": True}]
        return []
    if isinstance(event, (HumanMessage, ToolMessage)):
        return [sdk_user_message(event, session_id)]
    return []


# ── raw stream events ─────────────────────────────────────────────────────


@dataclass
class StreamEventSynthesizer:
    """
    Anthropic's raw stream events from LangChain chunks, for `--include-partial-messages`:
    `message_start`, then per content block `content_block_start` / `_delta` / `_stop`, then
    `message_delta` (stop reason, usage) and `message_stop`.
    """

    model: str = ""
    message_id: str = field(default_factory=lambda: f"msg_{uuid_module.uuid4().hex}")
    _started: bool = False
    _index: int = -1
    _open: str | None = None
    _usage: dict[str, int] = field(default_factory=dict)
    _stop_reason: str | None = None

    def feed(self, chunk: AIMessageChunk) -> list[dict[str, Any]]:
        """The events one chunk contributes."""
        events: list[dict[str, Any]] = []
        if not self._started:
            self._started = True
            events.append({"type": "message_start", "message": {
                "id": self.message_id, "type": "message", "role": "assistant", "model": self.model or _model_of(chunk),
                "content": [], "stop_reason": None, "stop_sequence": None, "usage": sdk_usage(chunk)}})
        for kind, payload in _chunk_parts(chunk):
            # A tool call's first fragment carries its id and opens a block; the rest continue it.
            opens = (payload.get("id") is not None or self._open is None) if kind == "tool" else self._open != kind
            if opens:
                events.extend(self._close())
                events.append(self._start_block(kind, payload))
                self._open = f"tool:{payload.get('id')}" if kind == "tool" else kind
            events.append(self._delta(kind, payload))
        if chunk.usage_metadata:
            self._usage = sdk_usage(chunk)
        meta = chunk.response_metadata or {}
        reason = meta.get("stop_reason") or meta.get("finish_reason")
        if reason:
            self._stop_reason = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}.get(
                str(reason), str(reason))
        return events

    def finish(self) -> list[dict[str, Any]]:
        """The events that end the message (nothing if no chunk arrived)."""
        if not self._started:
            return []
        events = self._close()
        events.append({"type": "message_delta", "delta": {"stop_reason": self._stop_reason, "stop_sequence": None},
                       "usage": {"output_tokens": self._usage.get("output_tokens", 0)}})
        events.append({"type": "message_stop"})
        return events

    def _start_block(self, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._index += 1
        if kind == "tool":
            block: dict[str, Any] = {"type": "tool_use", "id": str(payload.get("id") or ""),
                                     "name": str(payload.get("name") or ""), "input": {}}
        elif kind == "thinking":
            block = {"type": "thinking", "thinking": "", "signature": ""}
        else:
            block = {"type": "text", "text": ""}
        return {"type": "content_block_start", "index": self._index, "content_block": block}

    def _delta(self, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if kind == "tool":
            delta = {"type": "input_json_delta", "partial_json": str(payload.get("args") or "")}
        elif kind == "thinking":
            delta = {"type": "thinking_delta", "thinking": str(payload.get("thinking") or "")}
        else:
            delta = {"type": "text_delta", "text": str(payload.get("text") or "")}
        return {"type": "content_block_delta", "index": self._index, "delta": delta}

    def _close(self) -> list[dict[str, Any]]:
        if self._open is None:
            return []
        self._open = None
        return [{"type": "content_block_stop", "index": self._index}]


def _chunk_parts(chunk: AIMessageChunk) -> Iterable[tuple[str, Mapping[str, Any]]]:
    """A chunk's pieces in order: text, thinking and tool-call argument fragments."""
    content = chunk.content
    if isinstance(content, str):
        if content:
            yield "text", {"text": content}
    else:
        for block in content:
            if isinstance(block, str):
                if block:
                    yield "text", {"text": block}
            elif isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                yield "text", {"text": block["text"]}
            elif isinstance(block, dict) and block.get("type") == "thinking" and block.get("thinking"):
                yield "thinking", {"thinking": block["thinking"]}
    for piece in chunk.tool_call_chunks or ():
        yield "tool", {"id": piece.get("id"), "name": piece.get("name"), "args": piece.get("args") or "",
                       "index": piece.get("index")}


def stream_event_message(event: Mapping[str, Any], session_id: str) -> dict[str, Any]:
    """QueryEngine's `stream_event` wrapper."""
    return {"type": "stream_event", "event": dict(event), "session_id": session_id, "parent_tool_use_id": None,
            "uuid": _uuid()}


__all__ = [
    "AGENT_TOOL_NAME",
    "LEGACY_AGENT_TOOL_NAME",
    "StreamEventSynthesizer",
    "SystemInitInputs",
    "assistant_content_blocks",
    "build_system_init_message",
    "sdk_api_retry",
    "sdk_assistant_message",
    "sdk_compact_boundary",
    "sdk_compat_tool_name",
    "sdk_messages_for_event",
    "sdk_result_message",
    "sdk_status",
    "sdk_tdd_message",
    "sdk_tool_use_summary",
    "sdk_usage",
    "sdk_user_message",
    "stream_event_message",
    "user_message_content",
]
