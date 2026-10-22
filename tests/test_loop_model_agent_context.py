"""Phase 7: the per-context request — a worker's own system prompt, the CLAUDE.md and git
status omissions, and the coordinator's extra user context (`runAgent.ts`, `query()`)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.loop.config import build_run_config
from app.loop.context import AppStateStore, assembly, tool_context_for
from app.loop.context.assembly import (
    AGENT_NOTES,
    SessionContext,
    compute_env_info,
    enhance_system_prompt_with_env_details,
)
from app.loop.model import (
    build_request_messages,
    request_system_context,
    request_system_texts,
    request_user_context,
)
from app.loop.state import initial_loop_state

CONFIG = build_run_config("run-ctx", postgres_checkpointing=False)


@pytest.fixture(autouse=True)
def _session(monkeypatch):
    """One memoized session for the run, as the main thread and its workers share it."""
    session = SessionContext(
        user_context={"claudeMd": "PROJECT RULES", "currentDate": "Today's date is 2026-10-22."},
        system_context={"gitStatus": "STATUS"},
    )
    monkeypatch.setattr(assembly, "_SESSIONS", {CONFIG.run_id: session})
    monkeypatch.setattr("app.loop.model.get_system_prompt", lambda ws, model: ["unused"])
    monkeypatch.setattr("app.loop.model.resolve_system_prompt_sections", lambda sections, cache: ["DEFAULT PROMPT"])
    return session


def _state(**context: Any):
    ctx = tool_context_for(AppStateStore(), **context)
    return initial_loop_state((HumanMessage(content="task"),), ctx)


def test_the_main_thread_request_is_unchanged():
    state = _state()
    assert request_system_texts(state, CONFIG) == ["DEFAULT PROMPT", "gitStatus: STATUS"]
    assert request_user_context(state, CONFIG) == {
        "claudeMd": "PROJECT RULES", "currentDate": "Today's date is 2026-10-22."}


def test_an_agent_prompt_replaces_the_default_sections_and_keeps_the_system_context():
    state = _state(agent_system_prompt=("I am the tester.", "Notes"))
    assert request_system_texts(state, CONFIG) == ["I am the tester.", "Notes", "gitStatus: STATUS"]


def test_an_empty_agent_prompt_still_replaces_the_default():
    state = _state(agent_system_prompt=())
    assert request_system_texts(state, CONFIG) == ["gitStatus: STATUS"]


def test_omit_git_status_drops_it_from_the_system_context_only():
    state = _state(omit_git_status=True)
    assert request_system_context(state, CONFIG) == {}
    assert request_system_texts(state, CONFIG) == ["DEFAULT PROMPT"]


def test_omit_claude_md_drops_only_the_memory_files():
    state = _state(omit_claude_md=True)
    assert request_user_context(state, CONFIG) == {"currentDate": "Today's date is 2026-10-22."}


def test_extra_user_context_is_merged_after_the_session_context():
    state = _state(extra_user_context={"workerToolsContext": "Workers spawned via the Agent tool ..."})
    assert list(request_user_context(state, CONFIG).items())[-1] == (
        "workerToolsContext", "Workers spawned via the Agent tool ...")
    request = build_request_messages(state, CONFIG)
    assert "# workerToolsContext\nWorkers spawned via the Agent tool ..." in str(request[1].content)


def test_overrides_never_leak_into_the_shared_session(_session):
    """Workers share the run id, hence the memoized session: filtering is per request."""
    _state(omit_claude_md=True, omit_git_status=True, extra_user_context={"k": "v"})
    worker = _state(omit_claude_md=True, omit_git_status=True, extra_user_context={"k": "v"},
                    agent_system_prompt=("WORKER",))
    request_system_texts(worker, CONFIG)
    request_user_context(worker, CONFIG)
    assert _session.user_context == {"claudeMd": "PROJECT RULES", "currentDate": "Today's date is 2026-10-22."}
    assert _session.system_context == {"gitStatus": "STATUS"}
    main = _state()
    assert request_system_texts(main, CONFIG) == ["DEFAULT PROMPT", "gitStatus: STATUS"]


def test_the_worker_request_starts_with_its_own_system_prompt():
    state = _state(agent_system_prompt=("WORKER",), omit_git_status=True)
    request = build_request_messages(state, CONFIG)
    assert request[0] == SystemMessage(content="WORKER")


def test_anthropic_breakpoints_cover_the_worker_prompt(monkeypatch):
    from app.config.config import Config
    from app.loop import model as model_mod
    from app.loop.retry import RetryContext

    seen: dict[str, Any] = {}

    class Fake:
        def astream(self, messages, config=None, **kwargs):
            seen["messages"], seen["kwargs"] = messages, kwargs
            return iter(())

    monkeypatch.delenv("TDDAGENTS_DISABLE_PROMPT_CACHING", raising=False)
    monkeypatch.setattr(Config, "CHAT_MODEL", "anthropic")
    monkeypatch.setattr(model_mod, "get_chat_model", lambda *a, **k: Fake())
    state = _state(agent_system_prompt=("WORKER",))
    model_mod.open_model_stream(state, CONFIG, RetryContext(model="m"))
    assert seen["messages"][0] == SystemMessage(content=[
        {"type": "text", "text": "WORKER\n\ngitStatus: STATUS", "cache_control": {"type": "ephemeral"}}])


class _Ws:
    root = "/w"

    def __init__(self, git: bool) -> None:
        self.git = git


def _fake_run(ws, cmd):
    if cmd.startswith("git rev-parse"):
        return (0, "true") if ws.git else (128, "")
    return 0, {"uname -s": "Linux", "uname -sr": "Linux 6.6.4"}.get(cmd, "")


def test_compute_env_info_is_upstreams_block(monkeypatch):
    monkeypatch.setattr(assembly, "_run", _fake_run)
    monkeypatch.setattr(assembly, "is_git_repo", lambda ws: ws.git)
    assert compute_env_info(_Ws(True), "o4-mini", ["/extra", "/more"]) == (
        "Here is useful information about the environment you are running in:\n"
        "<env>\n"
        "Working directory: /w\n"
        "Is directory a git repo: Yes\n"
        "Additional working directories: /extra, /more\n"
        "Platform: linux\n"
        "Shell: bash\n"
        "OS Version: Linux 6.6.4\n"
        "</env>\n"
        "You are powered by the model o4-mini."
    )
    assert "Additional working directories" not in compute_env_info(_Ws(False), "m", [])
    assert "Is directory a git repo: No\nPlatform: linux" in compute_env_info(_Ws(False), "m", [])


def test_enhance_appends_the_notes_and_the_env_info(monkeypatch):
    monkeypatch.setattr(assembly, "_run", _fake_run)
    monkeypatch.setattr(assembly, "is_git_repo", lambda ws: ws.git)
    out = enhance_system_prompt_with_env_details(["AGENT"], "m", ["/x"], _Ws(False))
    assert out == ("AGENT", AGENT_NOTES, compute_env_info(_Ws(False), "m", ["/x"]))
    assert AGENT_NOTES.startswith("Notes:\n- Agent threads always have their cwd reset between bash calls")
    assert AGENT_NOTES.endswith('should just be "Let me read the file." with a period.')
    assert enhance_system_prompt_with_env_details([], "m", (), _Ws(False))[0] == AGENT_NOTES


def test_tool_context_defaults_keep_todays_behavior():
    ctx = tool_context_for(AppStateStore())
    assert (ctx.agent_system_prompt, ctx.omit_claude_md, ctx.omit_git_status, ctx.extra_user_context) == (
        None, False, False, None)


class _ExecWs:
    """A workspace whose commands answer from a table; anything else fails."""

    def __init__(self, answers: dict[str, str], root: str | None = "/r") -> None:
        self.answers = answers
        if root is not None:
            self.root = root

    def execute(self, cmd):
        from types import SimpleNamespace

        if cmd in self.answers:
            return SimpleNamespace(exit_code=0, stdout=self.answers[cmd] + "\n")
        raise RuntimeError("no such command")


def test_compute_env_info_runs_its_commands_in_the_workspace():
    ws = _ExecWs({"uname -s": "Darwin", "uname -sr": "Darwin 23.1", "git rev-parse --is-inside-work-tree": "true"})
    out = compute_env_info(ws, "m")
    assert "Working directory: /r\nIs directory a git repo: Yes\nPlatform: darwin\n" in out
    assert "OS Version: Darwin 23.1\n" in out


def test_compute_env_info_reports_failures_as_unknown():
    out = compute_env_info(_ExecWs({}, root=None), "m")
    expected = ("Working directory: .\nIs directory a git repo: No\nPlatform: unknown\nShell: bash\n"
                "OS Version: unknown\n")
    assert expected in out


def test_compute_env_info_without_a_workspace():
    assert compute_env_info(None, "m") == (
        "Here is useful information about the environment you are running in:\n"
        "<env>\n"
        "Working directory: .\n"
        "Is directory a git repo: No\n"
        "Platform: unknown\n"
        "Shell: bash\n"
        "OS Version: unknown\n"
        "</env>\n"
        "You are powered by the model m."
    )


def test_omissions_tolerate_a_context_without_the_key(_session):
    _session.system_context.pop("gitStatus")
    _session.user_context.pop("claudeMd")
    state = _state(omit_git_status=True, omit_claude_md=True)
    assert request_system_context(state, CONFIG) == {}
    assert request_user_context(state, CONFIG) == {"currentDate": "Today's date is 2026-10-22."}


def test_a_fresh_run_reads_its_context_from_the_contexts_workspace(monkeypatch):
    seen: list[Any] = []
    monkeypatch.setattr(assembly, "_SESSIONS", {})

    def user(ws):
        seen.append(("user", ws))
        return {}

    def system(ws):
        seen.append(("system", ws))
        return {}

    monkeypatch.setattr(assembly, "get_user_context", user)
    monkeypatch.setattr(assembly, "get_system_context", system)
    ws = object()
    request_user_context(_state(workspace=ws), CONFIG)
    assert seen == [("user", ws), ("system", ws)]
    monkeypatch.setattr(assembly, "_SESSIONS", {})
    seen.clear()
    request_system_context(_state(workspace=ws), CONFIG)
    assert seen == [("user", ws), ("system", ws)]
