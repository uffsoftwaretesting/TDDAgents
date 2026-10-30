"""Phase 5: prompt-cache breakpoints, wired when the provider supports them (`claude.ts`)."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.services.api.claude import (
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
    import app.services.api.claude as model_mod
    from app.config.config import Config
    from app.query.config import build_run_config
    from app.query.state import initial_loop_state
    from app.services.api.with_retry import RetryContext
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for

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
    model_mod.open_model_stream(state, build_run_config("r"), RetryContext(model="m"))
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

    from app.query.config import build_run_config
    from app.query.state import initial_loop_state
    from app.services.api.claude import build_request_messages
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for

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
    request = build_request_messages(state, build_run_config("r"))
    assert isinstance(request[0], SystemMessage)
    sent = [m for m in request[1:] if not str(m.content).startswith("<system-reminder>\nAs you answer")]
    assert [m.content for m in sent] == [m.content for m in history]


def test_the_runs_cost_tracker_rides_on_the_model_call(monkeypatch):
    import app.services.api.claude as model_mod
    from app.config.config import Config
    from app.cost_tracker import CostTracker
    from app.query.config import build_run_config
    from app.query.state import initial_loop_state
    from app.services.api.with_retry import RetryContext
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for

    seen: dict = {}

    class FakeModel:
        def astream(self, messages, config=None, **kwargs):
            seen["config"] = config
            return iter(())

    monkeypatch.setattr(Config, "CHAT_MODEL", "openai")
    monkeypatch.setattr(model_mod, "get_chat_model", lambda *a, **k: FakeModel())
    monkeypatch.setattr(model_mod, "build_request_messages", lambda state, config: [HumanMessage(content="hi")])
    monkeypatch.setattr(model_mod, "request_system_texts", lambda state, config: [])
    run = build_run_config("r")
    tracker = CostTracker(model="m")
    ctx = tool_context_for(AppStateStore(), cost_tracker=tracker)
    model_mod.open_model_stream(initial_loop_state((HumanMessage(content="hi"),), ctx), run, RetryContext(model="m"))
    assert seen["config"]["callbacks"] == [model_mod.get_global_token_tracker(), tracker]
    plain = tool_context_for(AppStateStore())
    model_mod.open_model_stream(initial_loop_state((HumanMessage(content="hi"),), plain), run, RetryContext(model="m"))
    assert seen["config"]["callbacks"] == [model_mod.get_global_token_tracker()]


def test_the_provider_follows_the_requested_model(monkeypatch):
    """Phase 7: a worker resolved to a Claude alias reaches Anthropic even in an OpenAI run."""
    import app.services.api.claude as model_mod
    from app.config.config import Config
    from app.query.config import build_run_config
    from app.query.state import initial_loop_state
    from app.services.api.with_retry import RetryContext
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for

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
    run_config = build_run_config("r")
    model_mod.open_model_stream(state, run_config, RetryContext(model="claude-sonnet-4-6"))
    model_mod.open_model_stream(state, run_config, RetryContext(model="mystery"))
    assert asked == [("anthropic", "claude-sonnet-4-6"), ("openai", "mystery")]


def _stream_state():
    from app.query.state import initial_loop_state
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for

    return initial_loop_state((HumanMessage(content="hi"),), tool_context_for(AppStateStore()))


def _drain_stream(state, config):
    import asyncio

    import app.services.api.claude as model_mod

    async def go():
        return [m async for m in model_mod.stream_call_model(state, config)]

    return asyncio.run(go())


def test_stream_call_model_passes_the_runs_retry_options(monkeypatch, caplog):
    import dataclasses
    import logging

    import app.services.api.claude as model_mod
    from app.query.config import build_run_config
    from app.services.api.with_retry import RetryNotice

    seen: dict = {}

    async def fake_with_retry(operation, options):
        seen["options"] = options

        async def empty():
            return
            yield

        return empty()

    monkeypatch.setattr(model_mod, "with_retry", fake_with_retry)
    config = dataclasses.replace(build_run_config("r"), max_retries=3,
                                 fallback_model="fb")
    state = _stream_state()
    _drain_stream(state, config)
    options = seen["options"]
    assert (options.model, options.max_retries, options.fallback_model) == (config.model, 3, "fb")
    assert options.is_cancelled() is False
    state.tool_context.cancel.cancel()
    assert options.is_cancelled() is True
    with caplog.at_level(logging.WARNING, logger="app.services.api.claude"):
        options.on_notice(RetryNotice(attempt=2, max_retries=3, error=RuntimeError("boom"), delay_ms=0))
    assert "Model call attempt 2/4 failed: boom" in caplog.text


def test_a_failure_on_the_first_frame_is_retried(monkeypatch):
    """The first item is pulled inside the retried operation, so a stream that fails before
    emitting anything is still "before any output" and may be retried."""
    import dataclasses

    from langchain_core.messages import AIMessageChunk

    import app.services.api.claude as model_mod
    import app.services.api.with_retry as retry_mod
    from app.query.config import build_run_config

    attempts: list[int] = []

    def fake_open(state, config, context):
        attempts.append(1)
        failing = len(attempts) == 1

        async def stream():
            if failing:
                raise ConnectionError("reset")
            yield AIMessageChunk(content="hello")

        return stream()

    real_with_retry = retry_mod.with_retry

    async def no_sleep(_seconds):
        return None

    async def with_retry_no_sleep(operation, options):
        return await real_with_retry(operation, dataclasses.replace(options, sleep=no_sleep))

    monkeypatch.setattr(model_mod, "open_model_stream", fake_open)
    monkeypatch.setattr(model_mod, "with_retry", with_retry_no_sleep)
    out = _drain_stream(_stream_state(), build_run_config("r"))
    assert len(attempts) == 2
    assert [m.content for m in out] == ["hello"]


def test_a_model_without_a_config_kwarg_still_gets_the_cache_kwargs(monkeypatch):
    import app.services.api.claude as model_mod
    from app.config.config import Config
    from app.query.config import build_run_config
    from app.services.api.with_retry import RetryContext

    calls: list = []

    class OldModel:
        def astream(self, messages, **kwargs):
            calls.append(kwargs)
            if "config" in kwargs:
                raise TypeError("unexpected keyword argument 'config'")
            return iter(())

    monkeypatch.delenv("TDDAGENTS_DISABLE_PROMPT_CACHING", raising=False)
    monkeypatch.setattr(Config, "CHAT_MODEL", "anthropic")
    monkeypatch.setattr(model_mod, "get_chat_model", lambda *a, **k: OldModel())
    monkeypatch.setattr(model_mod, "build_request_messages", lambda state, config: [
        SystemMessage(content="s"), HumanMessage(content="hi")])
    monkeypatch.setattr(model_mod, "request_system_texts", lambda state, config: ["s"])
    model_mod.open_model_stream(_stream_state(), build_run_config("r"),
                                RetryContext(model="claude-sonnet-4-6"))
    assert calls[-1] == {"cache_control": {"type": "ephemeral"}}


def test_a_main_loop_model_override_is_the_requests_model(monkeypatch):
    """Upstream `options.mainLoopModel`: a skill's `model:` sets it, and the next call uses it."""
    import dataclasses

    import app.services.api.claude as model_mod
    from app.query.config import build_run_config

    seen: dict = {}

    async def fake_with_retry(operation, options):
        seen["options"] = options

        async def empty():
            return
            yield

        return empty()

    monkeypatch.setattr(model_mod, "with_retry", fake_with_retry)
    config = build_run_config("r")
    state = _stream_state()
    state = dataclasses.replace(state, tool_context=dataclasses.replace(state.tool_context, main_loop_model="o3"))
    _drain_stream(state, config)
    assert seen["options"].model == "o3"


def test_without_an_override_the_runs_model_is_used(monkeypatch):
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for

    assert tool_context_for(AppStateStore()).main_loop_model is None
    assert tool_context_for(AppStateStore(), main_loop_model="m").main_loop_model == "m"
    assert tool_context_for(AppStateStore(), skill_registry="r").skill_registry == "r"
