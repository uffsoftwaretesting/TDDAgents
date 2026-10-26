"""Phase 5 F2: post-compact cleanup and the production summarizer seam."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import app.loop.compact.post_compact_cleanup as pcc
import app.loop.compact.summarizer as summ
from app.loop.compact.compact import (
    PROMPT_TOO_LONG_ERROR_MESSAGE,
    create_compact_boundary_message,
)
from app.loop.compact.post_compact_cleanup import run_post_compact_cleanup
from app.loop.compact.prompt import COMPACT_SUMMARIZER_SYSTEM_PROMPT
from app.loop.compact.summarizer import build_compact_summarizer
from app.loop.context.app_state import AppStateStore
from app.loop.context.tool_context import tool_context_for
from tests.conftest import FakeWorkspace


def test_main_thread_cleanup_resets_the_whole_session_context(monkeypatch):
    calls: list[Any] = []
    monkeypatch.setattr(pcc, "invalidate_context_caches", lambda **kw: calls.append(kw))
    ctx = tool_context_for(AppStateStore(), run_id="r1", workspace=FakeWorkspace())
    run_post_compact_cleanup(ctx)
    assert calls == [{"run_id": "r1"}]
    calls.clear()
    run_post_compact_cleanup(ctx, query_source="sdk")
    run_post_compact_cleanup(ctx, query_source="repl_main_thread:x")
    assert calls == [{"run_id": "r1"}, {"run_id": "r1"}]


def test_worker_cleanup_clears_only_the_prompt_sections(monkeypatch):
    calls: list[Any] = []
    monkeypatch.setattr(pcc, "invalidate_context_caches", lambda **kw: calls.append(kw))
    sections = object()

    class Session:
        section_cache = sections

    seen: list[Any] = []

    def session_for(run_id: str, ws: Any) -> Session:
        seen.append((run_id, ws))
        return Session()

    monkeypatch.setattr(pcc, "get_session_context", session_for)
    ws = FakeWorkspace()
    worker = tool_context_for(AppStateStore(), run_id="r1", workspace=ws, agent_id="agent_1")
    run_post_compact_cleanup(worker)
    assert calls == [{"section_cache": sections}]
    assert seen == [("r1", ws)]
    calls.clear()
    run_post_compact_cleanup(tool_context_for(AppStateStore(), run_id="r1"), query_source="agent:x")
    assert calls[0].keys() == {"section_cache"}


def test_cleanup_without_a_run_does_nothing(monkeypatch):
    calls: list[Any] = []
    monkeypatch.setattr(pcc, "invalidate_context_caches", lambda **kw: calls.append(kw))
    run_post_compact_cleanup(tool_context_for(AppStateStore()))
    assert calls == []


# ── production summarizer (fake chat model, never a live LLM) ──────────────────

def _run(awaitable: Any) -> Any:
    async def wrapper() -> Any:
        return await awaitable

    return asyncio.run(wrapper())


class FakeChat:
    def __init__(self, reply: Any = None, raises: BaseException | None = None) -> None:
        self.reply = reply if reply is not None else AIMessage(content="summary text")
        self.raises = raises
        self.seen: list[Any] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.seen.append(messages)
        if self.raises is not None:
            raise self.raises
        return self.reply


def _install(monkeypatch, chat: FakeChat) -> list[Any]:
    built: list[Any] = []

    def factory(provider: str, **kwargs: Any) -> FakeChat:
        built.append((provider, kwargs))
        return chat

    monkeypatch.setattr(summ, "get_chat_model", factory)
    return built


def test_the_summarizer_sends_the_history_and_request_without_tools(monkeypatch):
    from app.config.config import Config

    chat = FakeChat()
    built = _install(monkeypatch, chat)
    summarizer = build_compact_summarizer(provider="openai", model="o4-mini")
    boundary = create_compact_boundary_message("auto", 1)
    history = [boundary, HumanMessage(content="q"), AIMessage(content="", tool_calls=[
        {"name": "ReadFile", "args": {"file_path": "a"}, "id": "c1"}]), ToolMessage(content="r", tool_call_id="c1")]
    request = HumanMessage(content="summarize")
    out = _run(summarizer(history, request, tool_context_for(AppStateStore()), max_output_tokens=20_000))
    assert out is chat.reply
    assert built == [("openai", {"model": "o4-mini", "temperature": Config.TEMPERATURE, "max_tokens": 20_000})]
    (sent,) = chat.seen
    assert sent[0] == SystemMessage(content=COMPACT_SUMMARIZER_SYSTEM_PROMPT)
    # The boundary marker is a system record, never sent (`normalizeMessagesForAPI`).
    assert sent[1:] == history[1:] + [request]


def test_the_summarizer_defaults_to_the_configured_provider_and_model(monkeypatch):
    from app.config.config import Config

    built = _install(monkeypatch, FakeChat())
    _run(build_compact_summarizer()([], HumanMessage(content="s"), None, max_output_tokens=7))
    assert built == [(Config.CHAT_MODEL, {"model": Config.MODEL, "temperature": Config.TEMPERATURE, "max_tokens": 7})]


@pytest.mark.parametrize("text", [
    "Error code: 400 - prompt is too long: 210000 tokens > 200000 maximum",
    "This model's maximum context length is 200000 tokens.",
    "context_length_exceeded",
])
def test_a_context_overflow_becomes_upstreams_prompt_too_long_reply(monkeypatch, text):
    _install(monkeypatch, FakeChat(raises=RuntimeError(text)))
    summarizer = build_compact_summarizer(provider="x", model="m")
    out = _run(summarizer([], HumanMessage(content="s"), None, max_output_tokens=1))
    assert out.content == f"{PROMPT_TOO_LONG_ERROR_MESSAGE}: {text}"
    assert out.additional_kwargs == {"is_api_error_message": True, "api_error": "prompt_too_long",
                                     "error_details": text}


def test_any_other_provider_error_propagates(monkeypatch):
    _install(monkeypatch, FakeChat(raises=RuntimeError("rate limited")))
    with pytest.raises(RuntimeError, match="^rate limited$"):
        summarizer = build_compact_summarizer(provider="x", model="m")
        _run(summarizer([], HumanMessage(content="s"), None, max_output_tokens=1))
