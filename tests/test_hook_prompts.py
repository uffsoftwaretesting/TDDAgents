"""The hook prompt protocol (`HOOK_PROMPTS`, Part 8d), against real shells where it talks to one."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.components.hooks.prompt_dialog import (
    PROMPT_CANCELLED,
    PromptCancelled,
    hook_prompts_enabled,
    parse_prompt_choice,
    render_prompt_dialog,
)
from app.schemas.hooks import CommandHook
from app.types.hooks import (
    PromptOption,
    PromptRequest,
    PromptResponse,
    parse_prompt_request,
)
from app.utils.hooks.hooks import exec_command_hook

REQUEST = {"prompt": "r1", "message": "Deploy to prod?",
           "options": [{"key": "y", "label": "Yes", "description": "ship it"}, {"key": "n", "label": "No"}]}


def _script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "hook.sh"
    path.write_text(body)
    return f"sh {path}"


def _run(tmp_path: Path, body: str, request_prompt: Any = None) -> Any:
    return asyncio.run(exec_command_hook(
        CommandHook(command=_script(tmp_path, body)), "PreToolUse", "PreToolUse:Bash", '{"tool_name": "Bash"}',
        None, cwd=str(tmp_path), project_dir=str(tmp_path), request_prompt=request_prompt))


# ── the request shape ────────────────────────────────────────────────────────

def test_a_prompt_request_is_recognised_by_its_shape():
    request = parse_prompt_request(REQUEST)
    assert request == PromptRequest(prompt="r1", message="Deploy to prod?", options=(
        PromptOption(key="y", label="Yes", description="ship it"), PromptOption(key="n", label="No")))
    bad: Any
    for bad in ({"message": "m", "options": []}, {"prompt": 1, "message": "m", "options": []},
                {"prompt": "p", "options": []}, {"prompt": "p", "message": "m", "options": "x"},
                {"prompt": "p", "message": "m", "options": ["x"]},
                {"prompt": "p", "message": "m", "options": [{"key": 1, "label": "l"}]},
                {"prompt": "p", "message": "m", "options": [{"key": "k"}]},
                {"prompt": "p", "message": "m", "options": [{"key": "k", "label": "l", "description": 3}]},
                [], "text"):
        assert parse_prompt_request(bad) is None
    assert PromptResponse(prompt_response="r1", selected="y").as_dict() == {"prompt_response": "r1", "selected": "y"}


# ── execCommandHook ──────────────────────────────────────────────────────────

ASKING_HOOK = f"""read input
echo 'before'
echo '{json.dumps(REQUEST)}'
read answer
echo "got: $answer"
echo '{{"prompt": "r2", "message": "Sure?", "options": [{{"key": "k", "label": "OK"}}]}}'
read second
echo "then: $second"
"""


def test_a_hook_asks_and_reads_the_answers_in_order(tmp_path):
    asked: list[PromptRequest] = []

    async def answer(request: PromptRequest) -> PromptResponse:
        asked.append(request)
        return PromptResponse(prompt_response=request.prompt, selected=request.options[0].key)

    result = _run(tmp_path, ASKING_HOOK, answer)
    assert [r.prompt for r in asked] == ["r1", "r2"]
    assert result.stdout == ('before\ngot: {"prompt_response":"r1","selected":"y"}\n'
                             'then: {"prompt_response":"r2","selected":"k"}\n')
    assert '"prompt": "r1"' in result.output  # only stdout drops the request lines
    assert result.status == 0


def test_a_failed_answer_closes_stdin(tmp_path):
    async def refuse(request: PromptRequest) -> PromptResponse:
        raise PromptCancelled(PROMPT_CANCELLED)

    body = f"""read input
echo '{json.dumps(REQUEST)}'
if read answer; then echo "answered"; else echo "closed"; fi
"""
    result = _run(tmp_path, body, refuse)
    assert result.stdout.strip() == "closed"


def test_without_request_prompt_stdin_closes_and_requests_stay_in_stdout(tmp_path):
    body = f"""read input
echo '{json.dumps(REQUEST)}'
if read answer; then echo "answered"; else echo "eof"; fi
"""
    result = _run(tmp_path, body)
    assert result.stdout.splitlines() == [json.dumps(REQUEST), "eof"]


def test_non_request_lines_pass_through(tmp_path):
    async def never(request: PromptRequest) -> PromptResponse:
        raise AssertionError("not a request")

    body = """read input
echo 'not json'
echo '{"decision": "approve"}'
echo ''
"""
    result = _run(tmp_path, body, never)
    assert result.stdout == 'not json\n{"decision": "approve"}\n\n'


# ── the binding: who asks ────────────────────────────────────────────────────

def test_pre_tool_use_hooks_get_the_tools_summary(monkeypatch):
    from app.utils.hooks.hooks import (
        execute_pre_tool_hooks,
        execute_user_prompt_submit_hooks,
    )

    calls: list[dict[str, Any]] = []

    class Dispatcher:
        def has_hook_for_event(self, event: Any) -> bool:
            return True

        def create_base_hook_input(self, *args: Any) -> dict[str, Any]:
            return {}

        def execute_hooks(self, hook_input: Any, tool_use_id: str, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return iter(())

    def bound(title: str, summary: str | None) -> Any:
        return lambda request: None

    context = SimpleNamespace(hook_dispatcher=Dispatcher(), request_prompt=bound, cancel=None,
                              agent_id=None, agent_type=None)
    monkeypatch.setattr("app.utils.hooks.hooks.dispatcher_of", lambda c: c.hook_dispatcher)
    execute_pre_tool_hooks(context, "Bash", "t1", {"command": "ls"}, "default", None, "List files")
    execute_user_prompt_submit_hooks(context, "hi", "default")
    assert (calls[0]["request_prompt"], calls[0]["tool_input_summary"]) == (bound, "List files")
    assert calls[1]["request_prompt"] is bound and "tool_input_summary" not in calls[1]


def test_execute_hooks_binds_the_hook_name_and_summary(tmp_path):
    from app.schemas.hooks import HookMatcher
    from app.utils.hooks.hooks import HookDispatcher
    from app.utils.hooks.hooks_config_snapshot import HooksConfigSnapshot

    hook = CommandHook(command=_script(tmp_path, f"read input\necho '{json.dumps(REQUEST)}'\nread a\n"))
    snapshot = HooksConfigSnapshot(hooks={"PreToolUse": (HookMatcher(matcher="Bash", hooks=(hook,)),)})
    dispatcher = HookDispatcher(snapshot=snapshot, session_id="s", cwd=str(tmp_path), project_dir=str(tmp_path))
    bindings: list[tuple[str, str | None]] = []

    def request_prompt(title: str, summary: str | None) -> Any:
        bindings.append((title, summary))

        async def answer(request: PromptRequest) -> PromptResponse:
            return PromptResponse(prompt_response=request.prompt, selected="n")

        return answer

    async def go() -> None:
        hook_input = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}
        async for _ in dispatcher.execute_hooks(hook_input, "t1", match_query="Bash", request_prompt=request_prompt,
                                                tool_input_summary="ls -la"):
            pass

    asyncio.run(go())
    assert bindings == [("PreToolUse:Bash", "ls -la")]


def test_a_tool_call_hands_its_summary_to_the_hooks(monkeypatch):
    from langchain_core.messages import AIMessage

    from app.services.tools.tool_execution import run_tool_use
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for
    from app.tools.bash_tool.bash_tool import build_bash_tool

    seen: list[tuple[str, str | None]] = []

    async def fake_pre(context: Any, name: str, args: Any, call_id: str, summary: str | None = None) -> Any:
        from app.services.tools.tool_hooks import PreToolHooksOutcome

        seen.append((name, summary))
        outcome = PreToolHooksOutcome()
        outcome.stopped = True
        return outcome

    monkeypatch.setattr("app.services.tools.tool_execution.run_pre_tool_use_hooks", fake_pre)
    bash = build_bash_tool()
    ctx = tool_context_for(AppStateStore())
    asyncio.run(run_tool_use({"name": "Bash", "args": {"command": "ls", "description": "List files"}, "id": "c1"},
                             AIMessage(content=""), ctx, [bash]))
    assert seen == [("Bash", "List files")]


# ── the REPL's dialog ────────────────────────────────────────────────────────

def test_the_gate_and_the_dialog():
    assert hook_prompts_enabled({"TDDAGENTS_HOOK_PROMPTS": "1"}) and not hook_prompts_enabled({})
    assert not hook_prompts_enabled({"TDDAGENTS_HOOK_PROMPTS": "0"})
    request = parse_prompt_request(REQUEST)
    assert request is not None
    assert render_prompt_dialog("PreToolUse:Bash", "ls -la", request) == (
        "⚠ PreToolUse:Bash  ls -la\nDeploy to prod?\n  1. Yes — ship it\n  2. No\nChoose 1-2.")
    assert render_prompt_dialog("UserPromptSubmit", None, request).startswith("⚠ UserPromptSubmit\n")
    assert parse_prompt_choice(request.options, " 2 ") == "n"
    assert parse_prompt_choice(request.options, "3") is None and parse_prompt_choice(request.options, "y") is None


def _repl(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> tuple[Any, list[str]]:
    from app.screens.repl import Repl

    if enabled:
        monkeypatch.setenv("TDDAGENTS_HOOK_PROMPTS", "1")
    else:
        monkeypatch.delenv("TDDAGENTS_HOOK_PROMPTS", raising=False)
    written: list[str] = []
    run = SimpleNamespace(ctx=SimpleNamespace(tools=(), command_queue=None, request_prompt=None), on_message=None)
    return Repl(run, SimpleNamespace(write=written.append)), written  # type: ignore[arg-type]


def test_the_repl_answers_a_hook_question(monkeypatch):
    repl, written = _repl(monkeypatch, True)
    assert repl.run.ctx.request_prompt == repl.request_prompt
    request = parse_prompt_request(REQUEST)

    async def go() -> Any:
        ask = repl.request_prompt("PreToolUse:Bash", "ls")
        pending = asyncio.ensure_future(ask(request))
        await asyncio.sleep(0)
        await repl.handle_line("9")
        await repl.handle_line("1")
        return await pending

    assert asyncio.run(go()) == PromptResponse(prompt_response="r1", selected="y")
    assert written[0].startswith("⚠ PreToolUse:Bash  ls") and written[1] == "Choose 1-2."


def test_esc_rejects_a_hook_question(monkeypatch):
    repl, _ = _repl(monkeypatch, True)
    request = parse_prompt_request(REQUEST)

    async def go() -> None:
        pending = asyncio.ensure_future(repl.request_prompt("UserPromptSubmit", None)(request))
        await asyncio.sleep(0)
        repl.interrupt()
        await pending

    with pytest.raises(PromptCancelled, match=PROMPT_CANCELLED):
        asyncio.run(go())


def test_without_the_opt_in_the_repl_never_prompts(monkeypatch):
    repl, _ = _repl(monkeypatch, False)
    assert repl.run.ctx.request_prompt is None
