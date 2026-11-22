"""
A response cut off by the output-token limit, as `reference/claude-code/src/` handles it:

* `services/api/claude.ts` `queryModel`, `message_delta` (l.2266-2292): a `max_tokens` stop yields
  `createAssistantAPIErrorMessage` "API Error: Claude's response exceeded the N output token maximum. …", a
  `model_context_window_exceeded` stop "API Error: The model has reached its context window limit.", both
  `apiError: 'max_output_tokens'`;
* `utils/messages.ts` `normalizeContentFromAPI` (l.2651-2694) and `utils/json.ts` `safeParseJSON`: a tool
  block's streamed input is parsed strictly, `{}` when it does not parse;
* `query.ts`: the error is withheld (l.820-827), and only when the model asked for no tool
  (`if (!needsFollowUp)`, l.1062) the turn is resumed with a meta message, `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT`
  (3, l.164) times, the count reset at `next_turn` (l.1720) and after a blocking stop hook (l.1291); once
  exhausted the withheld error is yielded (l.1254) and the API-error guard ends the turn;
* `utils/messages.ts` `normalizeMessagesForAPI` (l.2069): a synthetic API error is never sent to the model.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.config.config import Config
from app.context import reset_session_context
from app.query.config import build_run_config
from app.query.query import (
    MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
    OUTPUT_TOKEN_LIMIT_RECOVERY_MESSAGE,
)
from app.query.state import initial_loop_state
from app.query.stop_hooks import StopHookResult
from app.query.transitions import Continue, Terminal, Terminated, Transition
from app.query.withhold import is_max_output_tokens
from app.services.api.claude import stream_call_model
from app.state.app_state_store import AppState, AppStateStore
from app.tool import ToolResult, build_tool, tool_context_for
from app.utils.messages import (
    create_assistant_api_error_message,
    normalize_messages_for_api,
    tool_calls_in,
)
from app.utils.tokens import get_max_output_tokens_for_model
from tests.test_loop_engine import (
    FakeModel,
    FakeStopHooks,
    FakeTools,
    asks_for,
    collect,
    make_fake_deps,
    start,
)

UPSTREAM_RECOVERY_TEXT = (
    "Output token limit hit. Resume directly — no apology, no recap of what you were doing. "
    "Pick up mid-thought if that is where the cut happened. Break remaining work into smaller pieces."
)


@pytest.fixture(autouse=True)
def fresh_sessions(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TDDAGENTS_MAX_OUTPUT_TOKENS", raising=False)
    reset_session_context()
    yield
    reset_session_context()


# ── stream_call_model: the cut-off becomes upstream's API error ────────────────

class StreamModel:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks

    def bind_tools(self, tools: Any) -> StreamModel:
        return self

    async def astream(self, messages: Any, **_kwargs: Any) -> Any:
        for chunk in self.chunks:
            yield chunk


def _state(tools: tuple[Any, ...] = ()) -> Any:
    ctx = tool_context_for(AppStateStore(AppState()), tools=tools)
    return initial_loop_state(messages=(HumanMessage(content="go"),), tool_context=ctx)


def _stream(monkeypatch: pytest.MonkeyPatch, chunks: list[Any], tools: tuple[Any, ...] = ()) -> list[Any]:
    monkeypatch.setattr("app.services.api.claude.get_chat_model", lambda *a, **k: StreamModel(chunks))

    async def go() -> list[Any]:
        return [m async for m in stream_call_model(_state(tools), build_run_config(run_id="t"))]

    return asyncio.run(go())


def _stop(**metadata: Any) -> AIMessageChunk:
    """The last frame: `message_delta`'s stop reason (langchain_anthropic) or OpenAI's `finish_reason`."""
    return AIMessageChunk(content="", response_metadata=metadata)


def _flags(message: Any) -> dict[str, Any]:
    return dict(message.additional_kwargs)


def test_a_max_tokens_stop_yields_upstreams_error_after_the_answer(monkeypatch):
    out = _stream(monkeypatch, [AIMessageChunk(content="half an ans"), _stop(stop_reason="max_tokens")])
    assert [m.content for m in out[:-1]] == ["half an ans"]
    error = out[-1]
    limit = get_max_output_tokens_for_model(Config.MODEL)
    assert error.content == (
        f"API Error: Claude's response exceeded the {limit} output token maximum. To configure this behavior, "
        "set the TDDAGENTS_MAX_OUTPUT_TOKENS environment variable.")
    assert _flags(error) == {"is_api_error_message": True, "api_error": "max_output_tokens"}
    assert is_max_output_tokens(error) and not is_max_output_tokens(out[0])


def test_the_error_names_the_limit_the_request_was_sent_with(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_MAX_OUTPUT_TOKENS", "1234")
    out = _stream(monkeypatch, [AIMessageChunk(content="x"), _stop(stop_reason="max_tokens")])
    assert "exceeded the 1234 output token maximum" in out[-1].content


def test_a_context_window_stop_reuses_the_same_recovery(monkeypatch):
    out = _stream(monkeypatch, [AIMessageChunk(content="x"), _stop(stop_reason="model_context_window_exceeded")])
    assert out[-1].content == "API Error: The model has reached its context window limit."
    assert _flags(out[-1]) == {"is_api_error_message": True, "api_error": "max_output_tokens"}


def test_openais_length_finish_is_the_same_signal(monkeypatch):
    out = _stream(monkeypatch, [AIMessageChunk(content="x"), _stop(finish_reason="length")])
    assert _flags(out[-1])["api_error"] == "max_output_tokens"


@pytest.mark.parametrize("metadata", [{"stop_reason": "end_turn"}, {"stop_reason": "tool_use"},
                                      {"finish_reason": "stop"}, {}])
def test_any_other_stop_adds_nothing(monkeypatch, metadata):
    out = _stream(monkeypatch, [AIMessageChunk(content="done"), _stop(**metadata)])
    assert [m.content for m in out] == ["done"]


def test_a_stop_with_no_content_still_yields_the_error(monkeypatch):
    out = _stream(monkeypatch, [_stop(stop_reason="max_tokens")])
    assert len(out) == 1 and _flags(out[0])["api_error"] == "max_output_tokens"


def test_the_error_is_not_merged_into_the_cut_off_response(monkeypatch):
    """Upstream's error is its own message (`SYNTHETIC_MODEL`), not a block of the response it follows."""
    out = _stream(monkeypatch, [AIMessageChunk(content="x"), _stop(stop_reason="max_tokens")])
    assert out[0].id and out[-1].id != out[0].id


# ── strict parsing of a tool call's input ──────────────────────────────────────

def _write_tool() -> Any:
    return build_tool(name="WriteFile", prompt="w", call=lambda args, ctx: ToolResult(content=""),
                      input_schema={"type": "object", "properties": {"file_path": {"type": "string"},
                                                                     "content": {"type": "string"}},
                                    "required": ["file_path", "content"]})


def _call_chunks(*parts: str) -> list[AIMessageChunk]:
    first = AIMessageChunk(content="", tool_call_chunks=[{"name": "WriteFile", "args": "", "id": "call_1",
                                                          "index": 0}])
    rest = [AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": p, "id": None, "index": 0}])
            for p in parts]
    return [first, *rest]


def test_a_cut_off_tool_input_is_empty_as_upstreams_strict_parse(monkeypatch, caplog):
    chunks = [*_call_chunks('{"file_path": "/a.py", ', '"content": "print(1'), _stop(stop_reason="max_tokens")]
    with caplog.at_level("WARNING"):
        out = _stream(monkeypatch, chunks, tools=(_write_tool(),))
    (call,) = tool_calls_in(out[0])
    assert call["name"] == "WriteFile" and call["id"] == "call_1" and call["args"] == {}
    assert not getattr(out[0], "invalid_tool_calls", [])
    assert any("tool input JSON parse fail" in r.getMessage() and "WriteFile" in r.getMessage()
               for r in caplog.records)
    assert _flags(out[-1])["api_error"] == "max_output_tokens"


def test_a_complete_tool_input_is_parsed_unchanged(monkeypatch, caplog):
    with caplog.at_level("WARNING"):
        out = _stream(monkeypatch, _call_chunks('{"file_path": "/a.py", ', '"content": "x"}'),
                      tools=(_write_tool(),))
    assert tool_calls_in(out[0])[0]["args"] == {"file_path": "/a.py", "content": "x"}
    assert not any("parse fail" in r.getMessage() for r in caplog.records)


def test_a_tool_with_no_input_is_an_empty_object_without_a_warning(monkeypatch, caplog):
    with caplog.at_level("WARNING"):
        out = _stream(monkeypatch, _call_chunks(), tools=(_write_tool(),))
    assert tool_calls_in(out[0])[0]["args"] == {}
    assert not any("parse fail" in r.getMessage() for r in caplog.records)


def test_input_that_parses_to_something_other_than_an_object_is_empty(monkeypatch):
    out = _stream(monkeypatch, _call_chunks("[1, 2]"), tools=(_write_tool(),))
    assert tool_calls_in(out[0])[0]["args"] == {}


# ── the request never carries a synthetic API error ────────────────────────────

def test_normalize_messages_for_api_drops_synthetic_api_errors():
    error = create_assistant_api_error_message("API Error: boom", error="max_output_tokens")
    history = [HumanMessage(content="a"), AIMessage(content="b", id="r1"), error, HumanMessage(content="c")]
    sent = normalize_messages_for_api(history)
    assert error not in sent and [m.content for m in sent] == ["a", "b", "c"]
    assert history[2] is error  # the stored history is never rewritten


# ── the loop: withhold, recover without a tool call, then surface ──────────────

def _cut(text: str = "partial") -> list[Any]:
    return [AIMessage(content=text, id=f"r-{text}"),
            create_assistant_api_error_message("API Error: cut", error="max_output_tokens")]


def _recorder() -> tuple[list[Any], Any]:
    seen: list[Any] = []
    return seen, seen.append


def test_the_constant_and_the_text_are_upstreams():
    assert MAX_OUTPUT_TOKENS_RECOVERY_LIMIT == 3
    assert OUTPUT_TOKEN_LIMIT_RECOVERY_MESSAGE == UPSTREAM_RECOVERY_TEXT


def test_a_text_answer_cut_off_is_resumed_with_upstreams_meta_message():
    model = FakeModel([_cut("one"), [AIMessage(content="rest")]])
    events_seen, emit = _recorder()
    deps = make_fake_deps(model, emit_event=emit)
    events = collect(model, deps=deps)
    assert events[-1] == Terminated(reason=Terminal.COMPLETED)
    resumed = model.seen[1]
    nudge = resumed.messages[-1]
    assert isinstance(nudge, HumanMessage) and nudge.content == UPSTREAM_RECOVERY_TEXT
    assert nudge.additional_kwargs == {"is_meta": True}
    assert [m.content for m in resumed.messages[-3:-1]] == ["one", "API Error: cut"]
    assert resumed.max_output_tokens_recovery_count == 1 and resumed.turn_count == 1
    assert Transition(reason=Continue.MAX_OUTPUT_TOKENS_RECOVERY, attempt=1) in events_seen
    # Withheld: the error reaches nobody while recovery is still possible.
    assert not any(getattr(e, "content", None) == "API Error: cut" for e in events)


def test_recovery_runs_three_times_then_the_withheld_error_ends_the_turn():
    model = FakeModel([_cut("1"), _cut("2"), _cut("3"), _cut("4")])
    hooks = FakeStopHooks([StopHookResult(blocking_errors=(HumanMessage(content="finish the cycle"),))])
    events_seen, emit = _recorder()
    events = collect(model, deps=make_fake_deps(model, stop_hooks=hooks, emit_event=emit))
    assert [s.max_output_tokens_recovery_count for s in model.seen] == [0, 1, 2, 3]
    assert [t.attempt for t in events_seen if isinstance(t, Transition)] == [1, 2, 3]
    surfaced = [e for e in events if getattr(e, "content", None) == "API Error: cut"]
    assert len(surfaced) == 1
    assert events[-1] == Terminated(reason=Terminal.MODEL_ERROR)
    assert hooks.seen == []


def test_a_cut_off_response_with_a_tool_call_runs_the_tool_and_is_not_resumed():
    """`if (!needsFollowUp)`: the recovery is skipped, the tool runs, the error stays withheld."""
    error = create_assistant_api_error_message("API Error: cut", error="max_output_tokens")
    tools = FakeTools()
    model = FakeModel([[asks_for("WriteFile", "c1"), error], [AIMessage(content="done")]])
    events = collect(model, tools)
    assert tools.calls == [("WriteFile",)]
    assert events[-1] == Terminated(reason=Terminal.COMPLETED)
    nxt = model.seen[1]
    assert nxt.transition == Transition(reason=Continue.NEXT_TURN)
    assert not any(isinstance(m, HumanMessage) and m.content == UPSTREAM_RECOVERY_TEXT for m in nxt.messages)
    assert error in nxt.messages  # kept in history, stripped from the request by normalize_messages_for_api
    assert error not in events


def test_next_turn_resets_the_count():
    model = FakeModel([_cut("1"), _cut("2"), [asks_for("Bash", "c1")], _cut("3"), [AIMessage(content="ok")]])
    collect(model, FakeTools())
    assert [s.max_output_tokens_recovery_count for s in model.seen] == [0, 1, 2, 0, 1]


def test_a_blocking_stop_hook_resets_the_count():
    model = FakeModel([_cut("1"), [AIMessage(content="answer")], [AIMessage(content="again")]])
    hooks = FakeStopHooks([StopHookResult(blocking_errors=(HumanMessage(content="not yet"),))])
    collect(model, deps=make_fake_deps(model, stop_hooks=hooks))
    assert [s.max_output_tokens_recovery_count for s in model.seen] == [0, 1, 0]


def test_the_count_starts_at_zero():
    assert start().max_output_tokens_recovery_count == 0


def test_the_empty_input_fails_validation_naming_every_missing_parameter(monkeypatch):
    """What upstream's model is told after a cut-off call (`toolExecution.ts`, `formatZodValidationError`)."""
    from app.services.tools.tool_execution import run_tool_use

    ran: list[Any] = []

    def write(args: Any, ctx: Any) -> ToolResult:
        ran.append(args)
        return ToolResult(content="")

    tool = build_tool(name="WriteFile", prompt="w", call=write, input_schema=_write_tool().input_schema)
    chunks = [*_call_chunks('{"file_path": "/a.py", "content": "pri'), _stop(stop_reason="max_tokens")]
    out = _stream(monkeypatch, chunks, tools=(tool,))
    (call,) = tool_calls_in(out[0])
    ctx = tool_context_for(AppStateStore(AppState()), tools=(tool,))
    outcome = asyncio.run(run_tool_use(call, out[0], ctx, (tool,)))
    content = str(outcome.message.content)
    assert ran == [] and outcome.message.additional_kwargs["is_error"] is True
    assert "InputValidationError" in content
    assert "The required parameter `file_path` is missing" in content
    assert "The required parameter `content` is missing" in content
