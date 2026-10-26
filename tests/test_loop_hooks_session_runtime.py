"""
Tests for `app/loop/hooks/session.py` (SessionStart, UserPromptSubmit) and
`app/loop/hooks/runtime.py` (`build_hook_dispatcher`).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.loop.context.app_state import AppStateStore
from app.loop.context.tool_context import tool_context_for
from app.loop.hooks.exec_agent import exec_agent_hook
from app.loop.hooks.exec_http import HttpHookPolicy, exec_http_hook
from app.loop.hooks.exec_prompt import exec_prompt_hook
from app.loop.hooks.messages import HookAttachment, HookBlockingError
from app.loop.hooks.output import AggregatedHookResult
from app.loop.hooks.runtime import build_hook_dispatcher
from app.loop.hooks.schemas import CommandHook, HookMatcher
from app.loop.hooks.session import (
    process_session_start_hooks,
    process_user_prompt_submit_hooks,
)
from app.loop.permissions.settings import SettingsLocations
from tests.hook_doubles import ScriptedDispatcher

SUCCESS = HookAttachment(type="hook_success", hook_name="SessionStart:startup", tool_use_id="t",
                         hook_event="SessionStart", content="hello from hook")
HIDDEN = HookAttachment(type="hook_non_blocking_error", hook_name="x", tool_use_id="t", hook_event="SessionStart")


def reminder(text: str) -> str:
    return f"<system-reminder>\n{text}\n</system-reminder>"


# ── SessionStart ────────────────────────────────────────────────────────────

def test_session_start_without_dispatcher():
    out = asyncio.run(process_session_start_hooks(None, "startup"))
    assert out.messages == [] and out.initial_user_message is None


def test_session_start_messages_contexts_and_initial_message():
    d = ScriptedDispatcher(script={"SessionStart": [
        AggregatedHookResult(attachment=SUCCESS),
        AggregatedHookResult(attachment=HIDDEN),
        AggregatedHookResult(additional_contexts=("ctx one",)),
        AggregatedHookResult(additional_contexts=("ctx two",), initial_user_message="first turn"),
    ]})
    out = asyncio.run(process_session_start_hooks(d, "startup", agent_type="coordinator", model="m1"))
    assert [m.content for m in out.messages] == [
        reminder("SessionStart:startup hook success: hello from hook"),
        reminder("SessionStart hook additional context: ctx one\nctx two"),
    ]
    assert out.messages[1].additional_kwargs["attachment"]["toolUseID"] == "SessionStart"
    assert out.initial_user_message == "first turn"
    payload = d.payload("SessionStart")
    assert (payload["source"], payload["agent_type"], payload["model"]) == ("startup", "coordinator", "m1")


def test_session_start_without_contexts_adds_no_context_message():
    d = ScriptedDispatcher(script={"SessionStart": [AggregatedHookResult()]})
    out = asyncio.run(process_session_start_hooks(d, "compact"))
    assert out.messages == [] and out.initial_user_message is None
    payload = d.payload("SessionStart")
    assert payload["source"] == "compact"
    assert "agent_type" not in payload and "model" not in payload


# ── UserPromptSubmit ────────────────────────────────────────────────────────

def ups(script: list[AggregatedHookResult]) -> Any:
    d = ScriptedDispatcher(script={"UserPromptSubmit": script})
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=d)
    return asyncio.run(process_user_prompt_submit_hooks(ctx, "build a calculator", "acceptEdits")), d


def test_prompt_payload_and_default_outcome():
    out, d = ups([])
    assert out.should_query is True and out.messages == []
    assert out.blocked_message is None and out.stopped_message is None
    payload = d.payload("UserPromptSubmit")
    assert payload["prompt"] == "build a calculator"
    assert payload["permission_mode"] == "acceptEdits"


def test_prompt_blocked_erases_everything_else():
    out, _ = ups([AggregatedHookResult(additional_contexts=("kept?",)),
                  AggregatedHookResult(blocking_error=HookBlockingError(blocking_error="not allowed", command="c"))])
    assert out.should_query is False
    assert out.messages == []
    assert out.blocked_message == (
        "UserPromptSubmit operation blocked by hook:\nnot allowed\n\nOriginal prompt: build a calculator")
    assert out.stopped_message is None


def test_prompt_stopped_with_and_without_reason():
    out, _ = ups([AggregatedHookResult(prevent_continuation=True, stop_reason="maintenance")])
    assert out.should_query is False
    assert out.stopped_message == "Operation stopped by hook: maintenance"
    assert out.blocked_message is None
    out, _ = ups([AggregatedHookResult(prevent_continuation=True)])
    assert out.stopped_message == "Operation stopped by hook"


def test_prompt_context_and_success_messages_in_order():
    ok = HookAttachment(type="hook_success", hook_name="UserPromptSubmit", tool_use_id="t",
                        hook_event="UserPromptSubmit", content="rule: ask first")
    out, _ = ups([AggregatedHookResult(additional_contexts=("a", "b")),
                  AggregatedHookResult(attachment=ok),
                  AggregatedHookResult(attachment=HIDDEN)])
    assert out.should_query is True
    assert [m.content for m in out.messages] == [
        reminder("UserPromptSubmit hook additional context: a\nb"),
        reminder("UserPromptSubmit hook success: rule: ask first"),
    ]
    assert out.messages[0].additional_kwargs["attachment"]["toolUseID"] == "hook-UserPromptSubmit"


# ── build_hook_dispatcher ───────────────────────────────────────────────────

def write_settings(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_dispatcher_from_settings_with_injected_fakes(tmp_path: Path):
    write_settings(tmp_path / "proj" / ".tddagents" / "settings.json", {
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
        "allowedHttpHookUrls": ["https://hooks.example/*"],
        "httpHookAllowedEnvVars": ["TOKEN"],
    })
    locations = SettingsLocations(project_root=tmp_path / "proj", home=tmp_path / "home",
                                  managed_dir=tmp_path / "etc")
    caller = object()
    runner = object()
    transport = object()
    d = build_hook_dispatcher(locations, session_id="run-1", cwd="/ws", project_dir="/proj", model="m-1",
                              transcript_path="/t.jsonl", prompt_model_caller=caller,  # type: ignore[arg-type]
                              agent_runner=runner, http_transport=transport)  # type: ignore[arg-type]
    assert (d.session_id, d.cwd, d.project_dir, d.transcript_path) == ("run-1", "/ws", "/proj", "/t.jsonl")
    assert d.snapshot.matchers_for("Stop") == (HookMatcher(hooks=(CommandHook(command="echo hi"),)),)
    assert d.snapshot.managed_only is False and d.snapshot.disable_all is False
    assert d.prompt_executor.func is exec_prompt_hook  # type: ignore[union-attr]
    assert d.prompt_executor.keywords == {"model_caller": caller, "default_model": "m-1"}  # type: ignore[union-attr]
    assert d.agent_executor.func is exec_agent_hook  # type: ignore[union-attr]
    assert d.agent_executor.keywords == {  # type: ignore[union-attr]
        "agent_runner": runner, "transcript_path": "/t.jsonl", "default_model": "m-1"}
    assert d.http_executor.func is exec_http_hook  # type: ignore[union-attr]
    assert d.http_executor.keywords == {  # type: ignore[union-attr]
        "policy": HttpHookPolicy(allowed_urls=("https://hooks.example/*",), allowed_env_vars=("TOKEN",)),
        "transport": transport}
    assert d.registry.get_pending() == []


def test_policy_disable_all_reaches_the_snapshot(tmp_path: Path):
    write_settings(tmp_path / "etc" / "managed-settings.json", {"disableAllHooks": True})
    locations = SettingsLocations(project_root=tmp_path / "proj", home=tmp_path / "home",
                                  managed_dir=tmp_path / "etc")
    d = build_hook_dispatcher(locations, session_id="s", cwd=".", project_dir=".", model="m")
    assert d.snapshot.disable_all is True


def test_production_executors_are_built_when_none_are_injected(tmp_path: Path):
    locations = SettingsLocations(project_root=tmp_path, home=tmp_path / "home", managed_dir=tmp_path / "etc")
    d = build_hook_dispatcher(locations, session_id="s", cwd=".", project_dir=".", model="m")
    assert callable(d.prompt_executor.keywords["model_caller"])  # type: ignore[union-attr]
    assert callable(d.agent_executor.keywords["agent_runner"])  # type: ignore[union-attr]
    assert d.agent_executor.keywords["transcript_path"] == ""  # type: ignore[union-attr]
    assert d.http_executor.keywords["transport"] is None  # type: ignore[union-attr]
