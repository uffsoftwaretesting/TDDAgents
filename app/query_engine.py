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

`submit_message` is upstream's entry point: the prompt goes through `processUserInput`
(`app/utils/process_user_input/process_user_input.py`) — a typed skill, the user-input
attachments, the UserPromptSubmit hooks — and a turn that does not query (an unknown command, a
hook that stopped it) is recorded and answered with a result, never a loop. Every call whose
permission ends other than `allow` is kept for the result (`permissionDenials`, upstream's
`wrappedCanUseTool`), in the run's and its workers' calls alike. `interrupt` aborts the turn and
`get_messages` is the conversation. Not ported: `setModel`, `getReadFileState` and the one-shot
`ask()` wrapper, which only the SDK's control requests and per-prompt engines use; one engine
serves the whole session here (Phase 6).
"""

from __future__ import annotations

import time
import uuid as uuid_module
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.query.config import RunConfig
from app.query.deps import LoopDeps
from app.query.query import run_loop
from app.query.state import LoopState, initial_loop_state
from app.query.transitions import Terminated
from app.services.compact.compact import is_compact_boundary_message
from app.utils.attachments import attachment_of
from app.utils.conversation_recovery import NO_RESPONSE_REQUESTED
from app.utils.messages import Message
from app.utils.process_user_input.process_user_input import (
    ProcessUserInputResult,
    process_user_input,
)

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
    #: `permission_denials`: `{tool_name, tool_use_id, tool_input}` for every call not allowed.
    permission_denials: tuple[dict[str, Any], ...] = ()
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
        context: Any = None,
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
        #: Upstream `mutableMessages`: the conversation as recorded, kept across submits so a
        #: follow-up turn (a task notification, Phase 8) continues it; restarted at a boundary.
        self.mutable_messages: list[Message] = []
        #: Upstream's `permissionDenials`, kept for the engine's whole life.
        self.permission_denials: list[dict[str, Any]] = []
        #: The run's `ToolContext` (upstream's config: tools, `canUseTool`, app state, abort).
        self.context = context
        #: What `processUserInput` made of the last prompt `submit_message` took.
        self.input_result: ProcessUserInputResult | None = None
        #: A turn is in flight (upstream: an `abortController` exists for it).
        self.running = False
        if context is not None:
            context.record_permission_denial = self._record_denial

    def _record_denial(self, tool_name: str, tool_use_id: str, tool_input: Any) -> None:
        self.permission_denials.append({"tool_name": tool_name, "tool_use_id": tool_use_id, "tool_input": tool_input})

    def interrupt(self, reason: str | None = None) -> None:
        """
        `interrupt`: abort the turn in flight. `reason` is the abort signal's (`'interrupt'` for a
        message submitted while the turn ran, which the REPL and the `'now'` subscriber pass).
        """
        self.context.cancel.cancel(reason)

    def get_messages(self) -> list[Message]:
        """`getMessages`: the conversation as recorded."""
        return self.mutable_messages

    async def submit_message(
        self, prompt: str, *, prefix: Sequence[Message] = (), user_messages: Sequence[Message] | None = None
    ) -> AsyncIterator[Any]:
        """
        `submitMessage`: `processUserInput` on `prompt` after the conversation (and `prefix`, a
        first turn's SessionStart output), then the loop when it queries, else a result at once.
        `user_messages` replace the plain user message (an interrupted turn resumed).
        """
        from app.tool import CancelToken
        from app.tools.skill_tool.skill_tool import (
            context_with_allowed_tools,
            resolve_skill_model_override,
        )

        ctx = self.context
        if ctx.cancel.cancelled:
            # A fresh abort signal per turn, as upstream creates an `AbortController` per query: the
            # Esc that ended the last turn must not end this one.
            ctx.cancel = CancelToken()
        start = self._now()
        history = [*self.mutable_messages, *prefix]
        mode = str(ctx.get_app_state().tool_permission_context.mode)
        result = await process_user_input(
            prompt, ctx, history, permission_mode=mode, user_messages=user_messages, session_id=self.session_id,
            coordinator=self.config.gates.coordinator_mode,
        )
        self.input_result = result
        messages = [*history, *result.messages]
        if not result.should_query:
            self.mutable_messages = messages
            self._record(messages)
            self.terminal = None
            self.result = ResultMessage(
                subtype="success", is_error=False, duration_ms=round((self._now() - start) * 1000),
                duration_api_ms=0, num_turns=len(messages) - 1, stop_reason=None, session_id=self.session_id,
                total_cost_usd=0.0, usage=dict(_EMPTY_USAGE), model_usage={}, terminal_reason="",
                uuid=self._uuid(), result=result.result_text or "", permission_denials=tuple(self.permission_denials),
            )
            yield self.result
            return
        turn_ctx = context_with_allowed_tools(replace(ctx, messages=tuple(messages)), result.allowed_tools)
        if result.model:
            current = ctx.main_loop_model or self.config.model
            turn_ctx = replace(turn_ctx, main_loop_model=resolve_skill_model_override(result.model, current))
        async for event in self.submit(initial_loop_state(tuple(messages), turn_ctx)):
            yield event

    def _record(self, messages: list[Message]) -> None:
        if self.persist_session:
            self.transcript.record_transcript(messages)

    async def submit(self, state: LoopState) -> AsyncIterator[Any]:
        """`submitMessage`: run the loop from `state`, then yield the result."""
        start = self._now()
        self.mutable_messages = list(state.messages)
        self._record(self.mutable_messages)

        turn_count = 1
        last_stop_reason: str | None = None
        last_relevant: Message | None = None
        self.running = True
        try:
            async for event in run_loop(state, self.config, self.deps):
                if isinstance(event, BaseMessage):
                    boundary = is_compact_boundary_message(event)
                    if boundary or isinstance(event, (AIMessage, HumanMessage, ToolMessage)):
                        self.mutable_messages.append(event)
                        self._record(self.mutable_messages)
                    if boundary:
                        self.mutable_messages = [event]
                    if isinstance(event, (HumanMessage, ToolMessage)) and attachment_of(event) is None:
                        turn_count += 1
                    if isinstance(event, AIMessage):
                        last_stop_reason = normalize_stop_reason(event) or last_stop_reason
                    if _is_turn_relevant(event):
                        last_relevant = event
                elif isinstance(event, Terminated):
                    self.terminal = event
                yield event
        finally:
            self.running = False

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
            "permission_denials": tuple(self.permission_denials),
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
