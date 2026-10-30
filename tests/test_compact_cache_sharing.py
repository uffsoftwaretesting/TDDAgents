"""
`streamCompactSummary`'s cache-sharing path (`services/compact/compact.ts`): the fork first,
the regular summary request when it fails or has no text.

A live CPF run on Claude Haiku 5.5 (2026-10-29) showed why it matters: the regular request sends
the history under its own system prompt and without tools, and the API refused the thinking
blocks in it as "bound to a different conversation", so autocompaction never ran.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

import app.services.compact.summarizer as summarizer_module
from app.query.config import build_run_config
from app.query.state import initial_loop_state
from app.services.compact.auto_compact import AutoCompactOutcome
from app.services.compact.compact import (
    PROMPT_TOO_LONG_ERROR_MESSAGE,
    PTL_RETRY_MARKER,
    compact_conversation,
)
from app.services.compact.prompt import get_compact_prompt
from app.services.compact.summarizer import build_forked_compact_summarizer
from app.state.app_state_store import AppStateStore
from app.tool import tool_context_for
from tests.conftest import FakeWorkspace


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _ctx() -> Any:
    return tool_context_for(AppStateStore(), workspace=FakeWorkspace(files={}), run_id="r1")


class Recorder:
    """A fork or a regular summarizer: records each call, answers from its script."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[list[Any]] = []

    async def __call__(self, messages: Any, summary_request: Any, context: Any, **_kwargs: Any) -> Any:
        self.calls.append([*messages, summary_request])
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _compact(history: list[Any], fork: Any, regular: Any) -> Any:
    return _run(compact_conversation(history, _ctx(), summarizer=regular, trigger="auto",
                                     post_compact_attachments=lambda c: (), model="m", fork_summarizer=fork))


IMAGE = HumanMessage(content=[{"type": "image", "source": {"data": "x"}}, {"type": "text", "text": "hi"}])
HISTORY = [HumanMessage(content="build it"), AIMessage(content="ok", id="a1"), IMAGE]


def test_the_fork_summarizes_the_messages_as_the_main_loop_sends_them():
    fork, regular = Recorder(AIMessage(content="<summary>forked</summary>")), Recorder()
    result = _compact(HISTORY, fork, regular)
    # No image stripping: the prefix must be the main thread's (`forkContextMessages`).
    assert fork.calls == [[*HISTORY, HumanMessage(content=get_compact_prompt())]]
    assert regular.calls == []
    assert "forked" in result.summary_messages[0].content


def test_a_failed_fork_falls_back_to_the_regular_request():
    for reply in (RuntimeError("400 thinking block bound to a different conversation"), AIMessage(content=""),
                  AIMessage(content="API Error: x", additional_kwargs={"is_api_error_message": True})):
        fork, regular = Recorder(reply), Recorder(AIMessage(content="<summary>regular</summary>"))
        result = _compact(HISTORY, fork, regular)
        assert len(fork.calls) == 1
        # The regular path still prepares its messages: the image becomes a marker.
        assert regular.calls[0][2].content == [{"type": "text", "text": "[image]"}, {"type": "text", "text": "hi"}]
        assert "regular" in result.summary_messages[0].content


def test_without_a_fork_only_the_regular_request_runs():
    regular = Recorder(AIMessage(content="<summary>regular</summary>"))
    result = _run(compact_conversation(HISTORY, _ctx(), summarizer=regular, trigger="auto",
                                       post_compact_attachments=lambda c: (), model="m"))
    assert len(regular.calls) == 1 and "regular" in result.summary_messages[0].content


def test_a_prompt_too_long_retry_gives_the_fork_the_truncated_messages():
    history = [HumanMessage(content="q")] + [
        m for i in range(10) for m in (AIMessage(content="a" * 400, id=f"m{i}"), HumanMessage(content="u" * 400))]
    fork = Recorder(RuntimeError("down"), AIMessage(content="<summary>second</summary>"))
    regular = Recorder(AIMessage(content=f"{PROMPT_TOO_LONG_ERROR_MESSAGE}: too many"))
    result = _compact(history, fork, regular)
    assert len(fork.calls) == 2
    assert fork.calls[1][0].content == PTL_RETRY_MARKER
    assert len(fork.calls[1]) < len(fork.calls[0])
    assert "second" in result.summary_messages[0].content


def test_the_production_fork_is_one_main_loop_model_call_over_the_main_threads_state(monkeypatch):
    seen: list[Any] = []

    async def fake_stream(state: Any, config: Any) -> Any:
        seen.append((state, config))
        yield AIMessage(content=[{"type": "thinking", "thinking": "", "signature": "s"},
                                 {"type": "text", "text": "<summary>one</summary>"}], id="r")
        yield AIMessage(content="", id="r", tool_calls=[{"name": "ReadFile", "args": {}, "id": "t1"}])
        yield AIMessage(content=" two", id="r")

    monkeypatch.setattr(summarizer_module, "stream_call_model", fake_stream)
    ctx = _ctx()
    state = initial_loop_state((HumanMessage(content="old"),), tool_context=ctx)
    config = build_run_config(run_id="r1", postgres_checkpointing=False)
    request = HumanMessage(content="summarize")
    reply = _run(build_forked_compact_summarizer(state, config)(HISTORY, request, ctx))
    (fork_state, fork_config), = seen
    assert fork_state == dataclasses.replace(state, messages=(*HISTORY, request))
    assert fork_state.tool_context is ctx and fork_config is config
    # Only the text is the summary: thinking and the (never run) tool call are left out.
    assert reply.content == "<summary>one</summary> two"


def test_both_production_compactions_try_the_fork_first(monkeypatch):
    from app.query import deps

    seen: list[Any] = []

    async def fake_auto(messages: Any, context: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["fork_summarizer"])
        return AutoCompactOutcome(was_compacted=False)

    async def fake_compact(messages: Any, context: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["fork_summarizer"])
        raise RuntimeError("stop here")

    monkeypatch.setattr(deps, "auto_compact_if_needed", fake_auto)
    monkeypatch.setattr(deps, "compact_conversation", fake_compact)
    monkeypatch.setattr(deps, "build_forked_compact_summarizer", lambda state, config: ("fork", state, config))
    state = initial_loop_state((HumanMessage(content="old"),), tool_context=_ctx())
    config = build_run_config(run_id="r1", postgres_checkpointing=False)
    _run(deps.production_autocompact(state.messages, state, config))
    _run(deps.production_compact(state, config))
    assert seen == [("fork", state, config)] * 2
