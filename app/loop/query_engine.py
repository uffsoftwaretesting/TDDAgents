"""
One run's conversation owner (Phase 6): the `QueryEngine` equivalent.

Ported from `reference/claude-code/src/QueryEngine.ts` -> `QueryEngine.submitMessage`, the
headless path's wrapper around `query()`. It owns what the loop deliberately does not:

* **The transcript, written before the loop.** The starting messages are recorded before
  the model is first called, so a run killed mid-request is still resumable from the point
  its prompt was accepted. After that every whole message the loop yields is recorded on
  the growing history (`recordTranscript(messages)`): assistant and user messages, the
  compact boundary, and attachments (which the external build's `isLoggableMessage` drops
  at the store). A compact boundary restarts the history from itself, as upstream splices
  `messages` — which is what gives the boundary a null `parentUuid` on disk.
* **Usage and cost**, read from the run's cost tracker (`cost-tracker.ts`), which the model
  call feeds per API response.
* **The result message.** `isResultSuccessful` over the last assistant-or-user message
  decides `success` versus `error_during_execution`, exactly as upstream; the subtypes for
  turn, budget and structured-output ceilings do not exist because the ceilings do not.
  The loop's own `Terminal` reason is carried beside it as `terminal_reason`, because the
  TDD outcome is decided by the terminal and the ledger, never by the result subtype.

The loop's events pass through unchanged, `Terminated` included; the `ResultMessage` is
the last thing yielded, as upstream's `result` is.
"""

from __future__ import annotations

import time
import uuid as uuid_module
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.loop.attachments.types import attachment_of
from app.loop.compact.compact import is_compact_boundary_message
from app.loop.config import RunConfig
from app.loop.deps import LoopDeps
from app.loop.engine import run_loop
from app.loop.messages import Message
from app.loop.state import LoopState
from app.loop.storage.recovery import NO_RESPONSE_REQUESTED
from app.loop.transitions import Terminated

#: OpenAI's `finish_reason` in upstream's `stop_reason` vocabulary.
_FINISH_REASONS = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}

#: `SYNTHETIC_MESSAGES`: assistant texts that are bookkeeping, never a result. Upstream's
#: interrupt/cancel/reject texts are tool results or user messages in this port, so only
#: the resume sentinel can be an assistant's last text.
SYNTHETIC_MESSAGES: frozenset[str] = frozenset({NO_RESPONSE_REQUESTED})

_EMPTY_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
}


class TranscriptRecorder(Protocol):
    def record_transcript(self, messages: list[Message]) -> str | None: ...


@dataclass(frozen=True)
class ResultMessage:
    """`SDKResultMessage`, the fields a headless run can fill."""

    subtype: Literal["success", "error_during_execution"]
    is_error: bool
    duration_ms: int
    duration_api_ms: int
    num_turns: int
    stop_reason: str | None
    session_id: str
    total_cost_usd: float
    usage: dict[str, int]
    model_usage: dict[str, Any]
    terminal_reason: str
    uuid: str
    result: str | None = None
    errors: tuple[str, ...] = ()
    type: Literal["result"] = field(default="result")


def normalize_stop_reason(message: Any) -> str | None:
    """The message's stop reason in upstream's vocabulary (`end_turn`, `tool_use`, ...)."""
    meta = getattr(message, "response_metadata", None) or {}
    reason = meta.get("stop_reason")
    if reason:
        return str(reason)
    finish = meta.get("finish_reason")
    if not finish:
        return None
    return _FINISH_REASONS.get(str(finish), str(finish))


def _last_content_type(message: AIMessage) -> str:
    """`last(message.message.content)?.type ?? 'none'`, over a LangChain message."""
    if message.tool_calls:
        return "tool_use"
    content = message.content
    if isinstance(content, str):
        return "text" if content else "none"
    if not content:
        return "none"
    last = content[-1]
    if isinstance(last, dict):
        return str(last.get("type", "none"))
    # A LangChain content list holds only `str` and `dict` items: a bare string is text.
    return "text"


def is_result_successful(message: Message | None, stop_reason: str | None = None) -> bool:
    """`queryHelpers.ts` -> `isResultSuccessful`."""
    if message is None:
        return False
    if isinstance(message, AIMessage):
        return _last_content_type(message) in ("text", "thinking", "redacted_thinking")
    if isinstance(message, ToolMessage):
        return True
    return stop_reason == "end_turn"


def _is_turn_relevant(message: Any) -> bool:
    """`m.type === 'assistant' || m.type === 'user'` — an attachment is neither."""
    return isinstance(message, (AIMessage, HumanMessage, ToolMessage)) and attachment_of(message) is None


def _text_result(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        text = content
    elif content and isinstance(content[-1], dict) and content[-1].get("type") == "text":
        text = str(content[-1].get("text", ""))
    else:
        text = ""
    return "" if text in SYNTHETIC_MESSAGES else text


class QueryEngine:
    """One run of the loop with its transcript, usage and result (`QueryEngine`)."""

    def __init__(
        self,
        config: RunConfig,
        deps: LoopDeps,
        *,
        transcript: TranscriptRecorder,
        session_id: str,
        cost_tracker: Any = None,
        persist_session: bool = True,
        now: Callable[[], float] = time.time,
        uuid: Callable[[], str] = lambda: str(uuid_module.uuid4()),
    ) -> None:
        self.config = config
        self.deps = deps
        self.transcript = transcript
        self.session_id = session_id
        self.cost_tracker = cost_tracker
        self.persist_session = persist_session
        self._now = now
        self._uuid = uuid
        self.terminal: Terminated | None = None
        self.result: ResultMessage | None = None

    def _record(self, messages: list[Message]) -> None:
        if self.persist_session:
            self.transcript.record_transcript(messages)

    async def submit(self, state: LoopState) -> AsyncIterator[Any]:
        """`submitMessage`: run the loop from `state`, then yield the result."""
        start = self._now()
        messages: list[Message] = list(state.messages)
        self._record(messages)

        turn_count = 1
        last_stop_reason: str | None = None
        last_relevant: Message | None = None
        async for event in run_loop(state, self.config, self.deps):
            if isinstance(event, BaseMessage):
                boundary = is_compact_boundary_message(event)
                if boundary or isinstance(event, (AIMessage, HumanMessage, ToolMessage)):
                    messages.append(event)
                    self._record(messages)
                if boundary:
                    messages = [event]
                if isinstance(event, (HumanMessage, ToolMessage)) and attachment_of(event) is None:
                    turn_count += 1
                if isinstance(event, AIMessage):
                    last_stop_reason = normalize_stop_reason(event) or last_stop_reason
                if _is_turn_relevant(event):
                    last_relevant = event
            elif isinstance(event, Terminated):
                self.terminal = event
            yield event

        self.result = self._build_result(start, turn_count, last_stop_reason, last_relevant)
        yield self.result

    def _build_result(
        self, start: float, turn_count: int, stop_reason: str | None, result: Message | None
    ) -> ResultMessage:
        tracker = self.cost_tracker
        common: dict[str, Any] = {
            "duration_ms": round((self._now() - start) * 1000),
            "duration_api_ms": int(tracker.total_api_duration_ms) if tracker is not None else 0,
            "num_turns": turn_count,
            "stop_reason": stop_reason,
            "session_id": self.session_id,
            "total_cost_usd": float(tracker.total_cost_usd) if tracker is not None else 0.0,
            "usage": dict(tracker.total_usage()) if tracker is not None else dict(_EMPTY_USAGE),
            "model_usage": dict(tracker.model_usage()) if tracker is not None else {},
            "terminal_reason": str(self.terminal.reason) if self.terminal is not None else "",
            "uuid": self._uuid(),
        }
        if not is_result_successful(result, stop_reason):
            result_type = "undefined" if result is None else "assistant" if isinstance(result, AIMessage) else "user"
            content_type = _last_content_type(result) if isinstance(result, AIMessage) else "n/a"
            diagnostic = (
                f"[ede_diagnostic] result_type={result_type} last_content_type={content_type} "
                f"stop_reason={stop_reason}"
            )
            return ResultMessage(subtype="error_during_execution", is_error=True, errors=(diagnostic,), **common)
        text, is_api_error = "", False
        if isinstance(result, AIMessage):
            text = _text_result(result)
            is_api_error = result.additional_kwargs.get("is_api_error_message") is True
        return ResultMessage(subtype="success", is_error=is_api_error, result=text, **common)


__all__ = [
    "SYNTHETIC_MESSAGES",
    "QueryEngine",
    "ResultMessage",
    "TranscriptRecorder",
    "is_result_successful",
    "normalize_stop_reason",
]
