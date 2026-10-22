"""Phase 5: prompt-cache breakpoints, wired when the provider supports them (`claude.ts`)."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.loop.context.cache import (
    EPHEMERAL_CACHE_CONTROL,
    get_prompt_caching_enabled,
    prepare_cached_request,
    system_prompt_blocks_for_api,
)


def test_get_prompt_caching_enabled_follows_the_disable_env(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_DISABLE_PROMPT_CACHING", raising=False)
    assert get_prompt_caching_enabled() is True
    monkeypatch.setenv("TDDAGENTS_DISABLE_PROMPT_CACHING", "1")
    assert get_prompt_caching_enabled() is False
    monkeypatch.setenv("TDDAGENTS_DISABLE_PROMPT_CACHING", "0")
    assert get_prompt_caching_enabled() is True


def test_system_blocks_without_the_global_scope_are_one_org_block():
    """`splitSysPromptPrefix` without the first-party global-cache beta: everything joined into
    one block, cached with a plain ephemeral control (scope 'org' is the API default, so no
    `scope` field is sent)."""
    assert system_prompt_blocks_for_api(["a", "", "b"], enable_caching=True) == [
        {"type": "text", "text": "a\n\nb", "cache_control": {"type": "ephemeral"}},
    ]
    assert system_prompt_blocks_for_api(["a", "b"], enable_caching=False) == [{"type": "text", "text": "a\n\nb"}]
    assert system_prompt_blocks_for_api([], enable_caching=True) == []


def test_anthropic_requests_get_both_breakpoints(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_DISABLE_PROMPT_CACHING", raising=False)
    messages = [SystemMessage(content="sys"), HumanMessage(content="hi")]
    prepared, kwargs = prepare_cached_request(messages, provider="anthropic", system_texts=["s1", "s2"])
    assert prepared[0] == SystemMessage(content=[
        {"type": "text", "text": "s1\n\ns2", "cache_control": {"type": "ephemeral"}},
    ])
    assert prepared[1:] == messages[1:]
    # `addCacheBreakpoints`: the marker on the last message, which langchain-anthropic puts
    # on that message's last eligible block.
    assert kwargs == {"cache_control": {"type": "ephemeral"}}
    assert EPHEMERAL_CACHE_CONTROL == {"type": "ephemeral"}


def test_other_providers_and_disabled_caching_are_untouched(monkeypatch):
    messages = [SystemMessage(content="sys"), HumanMessage(content="hi")]
    assert prepare_cached_request(messages, provider="openai", system_texts=["s"]) == (messages, {})
    monkeypatch.setenv("TDDAGENTS_DISABLE_PROMPT_CACHING", "true")
    assert prepare_cached_request(messages, provider="anthropic", system_texts=["s"]) == (messages, {})


def _open_with(monkeypatch, provider: str):
    """Open a model stream through a fake chat model and capture what it was asked."""
    from app.config.config import Config
    from app.loop import model as model_mod
    from app.loop.config import build_run_config
    from app.loop.context import AppStateStore, tool_context_for
    from app.loop.retry import RetryContext
    from app.loop.state import initial_loop_state

    seen: dict = {}

    class FakeModel:
        def astream(self, messages, config=None, **kwargs):
            seen["messages"], seen["kwargs"] = messages, kwargs
            return iter(())

    monkeypatch.setattr(Config, "CHAT_MODEL", provider)
    monkeypatch.setattr(model_mod, "get_chat_model", lambda *a, **k: FakeModel())
    monkeypatch.setattr(model_mod, "build_request_messages", lambda state, config: [
        SystemMessage(content="joined"), HumanMessage(content="hi")])
    monkeypatch.setattr(model_mod, "request_system_texts", lambda state, config: ["static", "dynamic"])
    state = initial_loop_state((HumanMessage(content="hi"),), tool_context_for(AppStateStore()))
    model_mod.open_model_stream(state, build_run_config("r", postgres_checkpointing=False), RetryContext(model="m"))
    return seen


def test_the_stream_request_carries_the_breakpoints_for_anthropic(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_DISABLE_PROMPT_CACHING", raising=False)
    seen = _open_with(monkeypatch, "anthropic")
    assert seen["messages"][0] == SystemMessage(content=[
        {"type": "text", "text": "static\n\ndynamic", "cache_control": {"type": "ephemeral"}}])
    assert seen["kwargs"] == {"cache_control": {"type": "ephemeral"}}


def test_the_stream_request_is_unchanged_for_openai(monkeypatch):
    seen = _open_with(monkeypatch, "openai")
    assert seen["messages"][0] == SystemMessage(content="joined")
    assert seen["kwargs"] == {}


def test_the_request_sends_stored_history_as_is(monkeypatch, tmp_path):
    """Phase 5: compaction, the tool-result budget and attachments act on *stored* history
    in the engine (upstream's `messagesForQuery`); the request adds nothing per call."""
    from langchain_core.messages import AIMessage, ToolMessage

    from app.loop.config import build_run_config
    from app.loop.context import AppStateStore, tool_context_for
    from app.loop.model import build_request_messages
    from app.loop.state import initial_loop_state

    monkeypatch.setenv("HOME", str(tmp_path))
    big = "x" * 30_000
    history = (
        HumanMessage(content="t1"),
        AIMessage(content="", tool_calls=[{"name": "ReadFile", "args": {}, "id": "c1"}]),
        ToolMessage(content=big, tool_call_id="c1"),
        HumanMessage(content="t2"),
        AIMessage(content="", tool_calls=[{"name": "ReadFile", "args": {}, "id": "c2"}]),
        ToolMessage(content=big, tool_call_id="c2"),
        HumanMessage(content="t3"),
    )
    state = initial_loop_state(history, tool_context_for(AppStateStore()))
    request = build_request_messages(state, build_run_config("r", postgres_checkpointing=False))
    assert isinstance(request[0], SystemMessage)
    sent = [m for m in request[1:] if not str(m.content).startswith("<system-reminder>\nAs you answer")]
    assert [m.content for m in sent] == [m.content for m in history]


def test_the_runs_cost_tracker_rides_on_the_model_call(monkeypatch):
    from app.config.config import Config
    from app.loop import model as model_mod
    from app.loop.config import build_run_config
    from app.loop.context import AppStateStore, tool_context_for
    from app.loop.cost import CostTracker
    from app.loop.retry import RetryContext
    from app.loop.state import initial_loop_state

    seen: dict = {}

    class FakeModel:
        def astream(self, messages, config=None, **kwargs):
            seen["config"] = config
            return iter(())

    monkeypatch.setattr(Config, "CHAT_MODEL", "openai")
    monkeypatch.setattr(model_mod, "get_chat_model", lambda *a, **k: FakeModel())
    monkeypatch.setattr(model_mod, "build_request_messages", lambda state, config: [HumanMessage(content="hi")])
    monkeypatch.setattr(model_mod, "request_system_texts", lambda state, config: [])
    run = build_run_config("r", postgres_checkpointing=False)
    tracker = CostTracker(model="m")
    ctx = tool_context_for(AppStateStore(), cost_tracker=tracker)
    model_mod.open_model_stream(initial_loop_state((HumanMessage(content="hi"),), ctx), run, RetryContext(model="m"))
    assert seen["config"]["callbacks"] == [model_mod.get_global_token_tracker(), tracker]
    plain = tool_context_for(AppStateStore())
    model_mod.open_model_stream(initial_loop_state((HumanMessage(content="hi"),), plain), run, RetryContext(model="m"))
    assert seen["config"]["callbacks"] == [model_mod.get_global_token_tracker()]


def test_the_provider_follows_the_requested_model(monkeypatch):
    """Phase 7: a worker resolved to a Claude alias reaches Anthropic even in an OpenAI run."""
    from app.config.config import Config
    from app.loop import model as model_mod
    from app.loop.config import build_run_config
    from app.loop.context import AppStateStore, tool_context_for
    from app.loop.retry import RetryContext
    from app.loop.state import initial_loop_state

    asked: list = []

    class FakeModel:
        def astream(self, messages, config=None, **kwargs):
            return iter(())

    def fake_factory(provider, **kwargs):
        asked.append((provider, kwargs["model"]))
        return FakeModel()

    monkeypatch.setattr(Config, "CHAT_MODEL", "openai")
    monkeypatch.setattr(model_mod, "get_chat_model", fake_factory)
    monkeypatch.setattr(model_mod, "build_request_messages", lambda state, config: [HumanMessage(content="hi")])
    monkeypatch.setattr(model_mod, "request_system_texts", lambda state, config: ["s"])
    state = initial_loop_state((HumanMessage(content="hi"),), tool_context_for(AppStateStore()))
    run_config = build_run_config("r", postgres_checkpointing=False)
    model_mod.open_model_stream(state, run_config, RetryContext(model="claude-sonnet-4-6"))
    model_mod.open_model_stream(state, run_config, RetryContext(model="mystery"))
    assert asked == [("anthropic", "claude-sonnet-4-6"), ("openai", "mystery")]
