"""
Phase 5: the per-turn attachments pipeline (`app/loop/attachments/`), offline.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getAttachments`,
`getAttachmentMessages` and the sources this build has subsystems for, and
`src/utils/messages.ts` -> `normalizeAttachmentForAPI`.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.attachments import (
    AUTO_MODE_ATTACHMENT_CONFIG,
    TODO_REMINDER_CONFIG,
    Attachment,
    attachment_of,
    create_attachment_messages,
    create_tdd_state_attachment_message,
    get_attachment_messages,
    get_attachments,
    normalize_attachment_for_api,
)
from app.loop.attachments import dates as dates_mod
from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.context.file_state import FileState
from app.loop.hooks.async_registry import AsyncHookRegistry
from app.loop.ledger import PhaseLedger, TddPhase, TestRunRecord
from app.loop.messages import wrap_in_system_reminder
from app.loop.permissions.types import PermissionMode, ToolPermissionContext
from app.loop.tools.todo import TodoItem
from tests.conftest import FakeWorkspace


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def ctx_for(state: AppState | None = None, **kw: Any) -> Any:
    return tool_context_for(AppStateStore(state=state or AppState()), run_id=kw.pop("run_id", "run-x"), **kw)


def types_of(attachments: list[Attachment]) -> list[str]:
    return [a.type for a in attachments]


@pytest.fixture(autouse=True)
def _fresh_dates():
    dates_mod.reset_last_emitted_dates()
    yield
    dates_mod.reset_last_emitted_dates()


# ── messages: normalizeAttachmentForAPI ─────────────────────────────────────

def test_an_attachment_becomes_wrapped_meta_messages_carrying_the_attachment():
    att = Attachment("date_change", {"newDate": "2026-10-23"})
    (msg,) = create_attachment_messages(att)
    assert isinstance(msg, HumanMessage)
    assert msg.content == wrap_in_system_reminder(
        "The date has changed. Today's date is now 2026-10-23. DO NOT mention this to the user explicitly "
        "because they are already aware."
    )
    assert msg.additional_kwargs == {"is_meta": True, "attachment": {"type": "date_change", "newDate": "2026-10-23"}}
    assert attachment_of(msg) == {"type": "date_change", "newDate": "2026-10-23"}
    assert attachment_of(HumanMessage(content="x")) is None
    assert attachment_of(HumanMessage(content="x", additional_kwargs={"attachment": "nope"})) is None


def test_upstream_wording_for_each_ported_type():
    assert normalize_attachment_for_api(Attachment("edited_text_file", {"filename": "a.py", "snippet": "1\tx"})) == [
        (
            "Note: a.py was modified, either by the user or by a linter. This change was intentional, so make sure "
            "to take it into account as you proceed (ie. don't revert it unless the user asks you to). Don't tell "
            "the user this, since they are already aware. Here are the relevant changes (shown with line numbers):"
            "\n1\tx"
        )
    ]
    assert normalize_attachment_for_api(
        Attachment("nested_memory", {"path": "src/TDDAGENTS.md", "content": "rule"})
    ) == ["Contents of src/TDDAGENTS.md:\n\nrule"]
    assert normalize_attachment_for_api(
        Attachment("queued_command", {"prompt": "done", "commandMode": "task-notification"})
    ) == ["A background agent completed a task:\ndone"]
    assert normalize_attachment_for_api(Attachment("queued_command", {"prompt": "hi", "commandMode": "prompt"})) == [
        (
            "The user sent a new message while you were working:\nhi\n\nIMPORTANT: After completing your current "
            "task, you MUST address the user's message above. Do not ignore it."
        )
    ]
    assert normalize_attachment_for_api(Attachment("token_usage", {"used": 5, "total": 10, "remaining": 5})) == [
        "Token usage: 5/10; 5 remaining"
    ]
    assert normalize_attachment_for_api(Attachment("auto_mode", {"reminderType": "sparse"})) == [
        (
            "Auto mode still active (see full instructions earlier in conversation). Execute autonomously, "
            "minimize interruptions, prefer action over planning."
        )
    ]
    full = normalize_attachment_for_api(Attachment("auto_mode", {"reminderType": "full"}))[0]
    assert full.startswith("## Auto Mode Active\n\nAuto mode is active.")
    assert "6. **Avoid data exfiltration**" in full
    assert normalize_attachment_for_api(Attachment("unknown_kind", {})) == []


def test_todo_reminder_wording_with_and_without_items():
    base = (
        "The TodoWrite tool hasn't been used recently. If you're working on tasks that would benefit from "
        "tracking progress, consider using the TodoWrite tool to track progress. Also consider cleaning up the "
        "todo list if has become stale and no longer matches what you are working on. Only use it if it's "
        "relevant to the current work. This is just a gentle reminder - ignore if not applicable. Make sure "
        "that you NEVER mention this reminder to the user\n"
    )
    assert normalize_attachment_for_api(Attachment("todo_reminder", {"content": [], "itemCount": 0})) == [base]
    items = [{"content": "write test", "status": "pending"}, {"content": "code", "status": "in_progress"}]
    assert normalize_attachment_for_api(Attachment("todo_reminder", {"content": items, "itemCount": 2})) == [
        base + "\n\nHere are the existing contents of your todo list:\n\n"
        "[1. [pending] write test\n2. [in_progress] code]"
    ]


def test_deltas_wording():
    initial = Attachment("agent_listing_delta", {
        "addedTypes": ["tester"], "addedLines": ["- tester: writes tests (Tools: All tools)"],
        "removedTypes": [], "isInitial": True, "showConcurrencyNote": True,
    })
    assert normalize_attachment_for_api(initial) == [
        (
            "Available agent types for the Agent tool:\n- tester: writes tests (Tools: All tools)\n\n"
            "Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a "
            "single message with multiple tool uses."
        )
    ]
    later = Attachment("agent_listing_delta", {
        "addedTypes": ["dev"], "addedLines": ["- dev: x (Tools: All tools)"], "removedTypes": ["old"],
        "isInitial": False, "showConcurrencyNote": True,
    })
    assert normalize_attachment_for_api(later) == [
        (
            "New agent types are now available for the Agent tool:\n- dev: x (Tools: All tools)\n\n"
            "The following agent types are no longer available:\n- old"
        )
    ]
    mcp = Attachment("mcp_instructions_delta", {"addedNames": ["s"], "addedBlocks": ["## s\nuse it"],
                                                "removedNames": ["gone"]})
    assert normalize_attachment_for_api(mcp) == [
        (
            "# MCP Server Instructions\n\nThe following MCP servers have provided instructions for how to use "
            "their tools and resources:\n\n## s\nuse it\n\nThe following MCP servers have disconnected. Their "
            "instructions above no longer apply:\ngone"
        )
    ]


def test_async_hook_response_renders_system_message_then_context():
    att = Attachment("async_hook_response", {
        "response": {
            "systemMessage": "sys",
            "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "ctx"},
        },
    })
    assert normalize_attachment_for_api(att) == ["sys", "ctx"]
    assert normalize_attachment_for_api(Attachment("async_hook_response", {"response": {}})) == []


# ── the pipeline ─────────────────────────────────────────────────────────────

def test_a_fresh_run_says_only_the_tdd_state_and_then_nothing():
    ctx = ctx_for()
    assert types_of(run(get_attachments(ctx, []))) == ["tdd_state"]
    history = run(get_attachment_messages(ctx, []))
    assert run(get_attachments(ctx, history)) == []
    assert run(get_attachment_messages(ctx, history)) == []


def test_a_failing_source_is_dropped_not_raised(monkeypatch):
    from app.loop.attachments import pipeline

    def boom(*a: Any, **k: Any) -> list[Attachment]:
        raise RuntimeError("source broke")

    monkeypatch.setattr(pipeline, "get_date_change_attachments", boom)
    assert types_of(run(get_attachments(ctx_for(), []))) == ["tdd_state"]


def test_disable_attachments_keeps_only_queued_commands(monkeypatch):
    from app.loop.command_queue import CommandQueue, QueuedCommand

    queue = CommandQueue()
    queue.enqueue(QueuedCommand(value="note", mode="prompt"))
    monkeypatch.setenv("TDDAGENTS_DISABLE_ATTACHMENTS", "1")
    ctx = ctx_for(AppState(phase_ledger=PhaseLedger()))
    ctx = replace(ctx, command_queue=queue)
    got = run(get_attachments(ctx, []))
    assert types_of(got) == ["queued_command"]


def test_messages_are_produced_in_attachment_order(monkeypatch):
    from app.loop.attachments import pipeline

    monkeypatch.setattr(pipeline, "get_date_change_attachments",
                        lambda messages, context: [Attachment("date_change", {"newDate": "d"})])
    msgs = run(get_attachment_messages(ctx_for(), []))
    kinds = [attachment_of(m)["type"] for m in msgs]  # type: ignore[index]
    assert kinds == ["date_change", "tdd_state"]


def test_the_skill_listing_follows_nested_memory(monkeypatch):
    """Upstream's order: `nested_memory`, then `skill_listing`, then `auto_mode`/`todo_reminders`."""
    from app.loop.attachments import pipeline

    monkeypatch.setattr(pipeline, "get_nested_memory_source",
                        lambda messages, context: [Attachment("nested_memory", {"path": "p", "content": "c"})])
    monkeypatch.setattr(pipeline, "get_skill_listing_attachments",
                        lambda messages, context: [Attachment("skill_listing", {"content": "- x", "skillCount": 1})])
    monkeypatch.setattr(pipeline, "get_todo_reminder_attachments",
                        lambda messages, context: [Attachment("todo_reminder", {"content": [], "itemCount": 0})])
    got = run(get_attachments(ctx_for(), [HumanMessage(content="t")]))
    assert types_of(got) == ["nested_memory", "skill_listing", "todo_reminder", "tdd_state"]


def test_agent_pending_messages_follow_todo_reminders_and_precede_the_tdd_state(monkeypatch):
    """Upstream's order: `todo_reminders`, then `agent_pending_messages`; `tdd_state` stays last."""
    from app.loop.attachments import pipeline

    monkeypatch.setattr(pipeline, "get_todo_reminder_attachments",
                        lambda messages, context: [Attachment("todo_reminder", {"content": [], "itemCount": 0})])
    monkeypatch.setattr(pipeline, "get_agent_pending_message_attachments",
                        lambda messages, context: [Attachment("queued_command", {"prompt": "from lead"})])
    got = run(get_attachments(ctx_for(), [HumanMessage(content="t")]))
    assert types_of(got) == ["todo_reminder", "queued_command", "tdd_state"]


# ── queued commands and async hook responses ─────────────────────────────────

def test_async_responses_come_from_the_registry():
    from app.loop.hooks.async_registry import AsyncHookResponse

    registry = AsyncHookRegistry()

    async def responses() -> list[AsyncHookResponse]:
        return [AsyncHookResponse(process_id="p", response={"systemMessage": "hi"}, hook_name="PostToolUse:Bash",
                                  hook_event="PostToolUse", stdout="", stderr="", exit_code=0)]

    registry.check_for_responses = responses  # type: ignore[method-assign]
    dispatcher = type("D", (), {"registry": registry})()
    ctx = replace(ctx_for(), hook_dispatcher=dispatcher)
    got = run(get_attachments(ctx, [HumanMessage(content="t")]))
    assert "async_hook_response" in types_of(got)


def test_async_responses_are_main_thread_only():
    from app.loop.hooks.async_registry import AsyncHookResponse

    registry = AsyncHookRegistry()
    calls: list[int] = []

    async def responses() -> list[AsyncHookResponse]:
        calls.append(1)
        return []

    registry.check_for_responses = responses  # type: ignore[method-assign]
    dispatcher = type("D", (), {"registry": registry})()
    worker = replace(ctx_for(), hook_dispatcher=dispatcher, agent_id="a1")
    run(get_attachments(worker, []))
    assert calls == []


# ── date_change ──────────────────────────────────────────────────────────────

def test_date_change_is_silent_on_first_sight_and_announces_a_new_day(monkeypatch):
    ctx = ctx_for(run_id="r-date")
    monkeypatch.setattr(dates_mod, "get_local_iso_date", lambda: "2026-10-22")
    assert dates_mod.get_date_change_attachments([], ctx) == []
    assert dates_mod.get_date_change_attachments([], ctx) == []
    monkeypatch.setattr(dates_mod, "get_local_iso_date", lambda: "2026-10-23")
    assert dates_mod.get_date_change_attachments([], ctx) == [Attachment("date_change", {"newDate": "2026-10-23"})]
    assert dates_mod.get_date_change_attachments([], ctx) == []


def test_the_last_emitted_date_is_per_run(monkeypatch):
    monkeypatch.setattr(dates_mod, "get_local_iso_date", lambda: "2026-10-22")
    dates_mod.get_date_change_attachments([], ctx_for(run_id="a"))
    monkeypatch.setattr(dates_mod, "get_local_iso_date", lambda: "2026-10-23")
    assert dates_mod.get_date_change_attachments([], ctx_for(run_id="b")) == []


def test_local_iso_date_shape():
    value = dates_mod.get_local_iso_date()
    assert len(value) == 10 and value[4] == "-" and value[7] == "-"


# ── changed_files ────────────────────────────────────────────────────────────

def _fs_ctx(files: dict[str, str], seen: dict[str, FileState]) -> Any:
    ws = FakeWorkspace(kind="local", files=files)
    ctx = ctx_for(workspace=ws)
    ctx.read_file_state.update(seen)
    return ctx


def test_a_file_edited_outside_the_agent_is_reported_once_with_a_snippet():
    ctx = _fs_ctx({"calc.py": "a = 1\nb = 3\n"}, {"calc.py": FileState(content="a = 1\nb = 2\n")})
    (att,) = run(get_attachments(ctx, []))[:1]
    assert att.type == "edited_text_file"
    assert att.data["filename"] == "calc.py"
    assert "b = 3" in att.data["snippet"]
    assert ctx.read_file_state["calc.py"] == FileState(content="a = 1\nb = 3\n")
    assert [a for a in run(get_attachments(ctx, [])) if a.type == "edited_text_file"] == []


def test_unchanged_partial_or_vanished_files_are_not_reported():
    ctx = _fs_ctx(
        {"same.py": "x\n", "part.py": "new\n"},
        {"same.py": FileState(content="x\n"), "part.py": FileState(content="old\n", offset=1, limit=1),
         "gone.py": FileState(content="y\n")},
    )
    assert [a for a in run(get_attachments(ctx, [])) if a.type == "edited_text_file"] == []
    assert "gone.py" not in ctx.read_file_state
    assert "part.py" in ctx.read_file_state


def test_changed_files_without_a_workspace_say_nothing():
    ctx = ctx_for()
    ctx.read_file_state["a.py"] = FileState(content="x")
    assert [a for a in run(get_attachments(ctx, [])) if a.type == "edited_text_file"] == []


# ── todo_reminders ───────────────────────────────────────────────────────────

def _todo_ctx(todos: tuple[TodoItem, ...] = ()) -> Any:
    from app.loop.tools.todo import build_todo_write_tool

    state = AppState(todos={"main": todos})
    return tool_context_for(AppStateStore(state=state), tools=(build_todo_write_tool(),))


def _turns(n: int) -> list[Any]:
    return [AIMessage(content=f"turn {i}") for i in range(n)]


def test_todo_reminder_cadence_is_upstreams():
    assert (TODO_REMINDER_CONFIG.TURNS_SINCE_WRITE, TODO_REMINDER_CONFIG.TURNS_BETWEEN_REMINDERS) == (10, 10)
    ctx = _todo_ctx((TodoItem(content="write test", status="pending", active_form="Writing test"),))
    assert [a for a in run(get_attachments(ctx, _turns(9))) if a.type == "todo_reminder"] == []
    (att,) = [a for a in run(get_attachments(ctx, _turns(10))) if a.type == "todo_reminder"]
    assert att.data == {"content": [{"content": "write test", "status": "pending", "activeForm": "Writing test"}],
                        "itemCount": 1}


def test_a_recent_todo_write_or_reminder_resets_the_count():
    ctx = _todo_ctx()
    write = AIMessage(content="", tool_calls=[{"id": "t", "name": "TodoWrite", "args": {}}])
    assert [a for a in run(get_attachments(ctx, [write, *_turns(9)])) if a.type == "todo_reminder"] == []
    reminder = create_attachment_messages(Attachment("todo_reminder", {"content": [], "itemCount": 0}))
    history = [*_turns(12), *reminder, *_turns(9)]
    assert [a for a in run(get_attachments(ctx, history)) if a.type == "todo_reminder"] == []


def test_thinking_only_messages_do_not_count_as_turns():
    ctx = _todo_ctx()
    thinking = [AIMessage(content=[{"type": "thinking", "thinking": "hm"}]) for _ in range(5)]
    assert [a for a in run(get_attachments(ctx, [*thinking, *_turns(9)])) if a.type == "todo_reminder"] == []


def test_no_reminder_without_the_tool_or_without_history():
    ctx = ctx_for()
    assert [a for a in run(get_attachments(ctx, _turns(20))) if a.type == "todo_reminder"] == []
    assert [a for a in run(get_attachments(_todo_ctx(), [])) if a.type == "todo_reminder"] == []


# ── auto_mode ────────────────────────────────────────────────────────────────

def _auto_ctx(mode: PermissionMode = PermissionMode.AUTO) -> Any:
    return ctx_for(AppState(tool_permission_context=ToolPermissionContext(mode=mode)))


def test_auto_mode_reminder_full_then_waits_five_turns_then_sparse():
    assert (AUTO_MODE_ATTACHMENT_CONFIG.TURNS_BETWEEN_ATTACHMENTS,
            AUTO_MODE_ATTACHMENT_CONFIG.FULL_REMINDER_EVERY_N_ATTACHMENTS) == (5, 5)
    ctx = _auto_ctx()
    (first,) = [a for a in run(get_attachments(ctx, [HumanMessage(content="go")])) if a.type == "auto_mode"]
    assert first.data == {"reminderType": "full"}
    history: list[Any] = [HumanMessage(content="go"), *create_attachment_messages(first)]
    history += [HumanMessage(content=f"u{i}") for i in range(4)]
    assert [a for a in run(get_attachments(ctx, history)) if a.type == "auto_mode"] == []
    history.append(HumanMessage(content="u5"))
    (second,) = [a for a in run(get_attachments(ctx, history)) if a.type == "auto_mode"]
    assert second.data == {"reminderType": "sparse"}


def test_meta_and_tool_result_messages_are_not_turns_for_auto_mode():
    ctx = _auto_ctx()
    first = Attachment("auto_mode", {"reminderType": "full"})
    history: list[Any] = [*create_attachment_messages(first)]
    history += [HumanMessage(content="m", additional_kwargs={"is_meta": True}) for _ in range(6)]
    history += [ToolMessage(content="r", tool_call_id="c") for _ in range(6)]
    assert [a for a in run(get_attachments(ctx, history)) if a.type == "auto_mode"] == []


def test_full_reminders_fall_on_the_first_sixth_eleventh():
    """`attachmentCount % FULL_REMINDER_EVERY_N_ATTACHMENTS === 1`: the 6th since the last exit is full."""
    ctx = _auto_ctx()
    history: list[Any] = []
    for _ in range(4):
        history += create_attachment_messages(Attachment("auto_mode", {"reminderType": "sparse"}))
        history += [HumanMessage(content=f"u{i}") for i in range(5)]
    (fifth,) = [a for a in run(get_attachments(ctx, history)) if a.type == "auto_mode"]
    assert fifth.data == {"reminderType": "sparse"}
    history += create_attachment_messages(fifth)
    history += [HumanMessage(content=f"u{i}") for i in range(5)]
    (att,) = [a for a in run(get_attachments(ctx, history)) if a.type == "auto_mode"]
    assert att.data == {"reminderType": "full"}


def test_no_auto_mode_reminder_outside_auto():
    assert [a for a in run(get_attachments(_auto_ctx(PermissionMode.ACCEPT_EDITS), [])) if a.type == "auto_mode"] == []


# ── token_usage ──────────────────────────────────────────────────────────────

def test_token_usage_is_env_gated_and_reads_the_last_response(monkeypatch):
    from app.loop.attachments import usage

    ctx = ctx_for()
    reply = AIMessage(content="x", usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                                                   "input_token_details": {"cache_read": 5, "cache_creation": 3}})
    assert [a for a in run(get_attachments(ctx, [reply])) if a.type == "token_usage"] == []
    monkeypatch.setenv("TDDAGENTS_ENABLE_TOKEN_USAGE_ATTACHMENT", "1")
    monkeypatch.setattr(usage, "effective_context_window", lambda model: 1000)
    (att,) = [a for a in run(get_attachments(ctx, [reply, HumanMessage(content="h")])) if a.type == "token_usage"]
    assert att.data == {"used": 128, "total": 1000, "remaining": 872}
    assert usage.token_count_from_last_api_response([HumanMessage(content="h")]) == 0


# ── agent_listing_delta / mcp_instructions_delta (gated off by default upstream) ─

def test_deltas_are_off_unless_their_env_gate_is_set(monkeypatch):
    from app.loop.agents.definition import AgentDefinition

    agent = AgentDefinition(name="tester", description="writes tests", prompt="p")
    agent_tool = type("T", (), {"name": "Agent", "aliases": ()})()
    ctx = replace(ctx_for(), tools=(agent_tool,))
    assert [a for a in run(get_attachments(ctx, [])) if a.type == "agent_listing_delta"] == []
    monkeypatch.setenv("TDDAGENTS_AGENT_LIST_IN_MESSAGES", "1")
    from app.loop.attachments import deltas

    monkeypatch.setattr(deltas, "agent_definitions_for", lambda context: (agent,))
    (att,) = [a for a in run(get_attachments(ctx, [])) if a.type == "agent_listing_delta"]
    assert att.data == {"addedTypes": ["tester"], "addedLines": ["- tester: writes tests (Tools: All tools)"],
                        "removedTypes": [], "isInitial": True, "showConcurrencyNote": True}
    history = create_attachment_messages(att)
    assert [a for a in run(get_attachments(ctx, history)) if a.type == "agent_listing_delta"] == []
    monkeypatch.setattr(deltas, "agent_definitions_for", lambda context: ())
    (removed,) = [a for a in run(get_attachments(ctx, history)) if a.type == "agent_listing_delta"]
    assert removed.data["removedTypes"] == ["tester"] and removed.data["isInitial"] is False


def test_agent_listing_requires_the_agent_tool(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_AGENT_LIST_IN_MESSAGES", "1")
    assert [a for a in run(get_attachments(ctx_for(), [])) if a.type == "agent_listing_delta"] == []


def test_agent_tools_description_matches_upstream():
    from app.loop.agents.definition import AgentDefinition
    from app.loop.attachments.deltas import format_agent_line

    def line(**kw: Any) -> str:
        return format_agent_line(AgentDefinition(name="a", description="d", prompt="p", **kw))

    assert line() == "- a: d (Tools: All tools)"
    assert line(tools=("ReadFile", "Grep")) == "- a: d (Tools: ReadFile, Grep)"
    assert line(disallowed_tools=("Bash",)) == "- a: d (Tools: All tools except Bash)"
    assert line(tools=("ReadFile", "Bash"), disallowed_tools=("Bash",)) == "- a: d (Tools: ReadFile)"
    assert line(tools=("Bash",), disallowed_tools=("Bash",)) == "- a: d (Tools: None)"


def test_mcp_instructions_delta(monkeypatch):
    from app.loop.attachments import deltas

    monkeypatch.setenv("TDDAGENTS_MCP_INSTR_DELTA", "1")
    clients = [{"name": "b", "type": "connected", "instructions": "use b"},
               {"name": "a", "type": "connected", "instructions": "use a"},
               {"name": "c", "type": "connected", "instructions": None},
               {"name": "d", "type": "pending", "instructions": "x"}]
    monkeypatch.setattr(deltas, "mcp_clients_for", lambda context: clients)
    (att,) = [a for a in run(get_attachments(ctx_for(), [])) if a.type == "mcp_instructions_delta"]
    assert att.data == {"addedNames": ["a", "b"], "addedBlocks": ["## a\nuse a", "## b\nuse b"], "removedNames": []}
    history = create_attachment_messages(att)
    monkeypatch.setattr(deltas, "mcp_clients_for", lambda context: clients[1:2])
    (later,) = [a for a in run(get_attachments(ctx_for(), history)) if a.type == "mcp_instructions_delta"]
    assert later.data == {"addedNames": [], "addedBlocks": [], "removedNames": ["b"]}


# ── tdd_state ────────────────────────────────────────────────────────────────

def test_tdd_state_is_announced_once_until_the_ledger_moves():
    ctx = ctx_for()
    (att,) = [a for a in run(get_attachments(ctx, [])) if a.type == "tdd_state"]
    assert att.data == {"phase": "RED", "red_confirmed": False, "green_passed": False}
    history = create_attachment_messages(att)
    assert history[0].content == wrap_in_system_reminder(
        "TDD phase ledger (written only by RunTests from observed test runs):\n"
        "Current Phase: RED\nRed Confirmed: False\nGreen Passed: False"
    )
    assert [a for a in run(get_attachments(ctx, history)) if a.type == "tdd_state"] == []
    moved = ctx_for(AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)))
    (again,) = [a for a in run(get_attachments(moved, history)) if a.type == "tdd_state"]
    assert again.data == {"phase": "GREEN", "red_confirmed": True, "green_passed": False}


def test_post_compaction_tdd_state_carries_the_runtests_evidence():
    ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
    record = TestRunRecord(test_path="tests/test_calc.py", exit_code=1, stdout="x" * 3000 + "\nE   assert 1 == 2",
                           stderr="")
    msg = create_tdd_state_attachment_message(ledger, record, after_compaction=True)
    text = str(msg.content)
    assert text.startswith("<system-reminder>\nTDD phase ledger (written only by RunTests from observed test runs)")
    assert "Current Phase: GREEN\nRed Confirmed: True\nGreen Passed: False" in text
    assert "Last RunTests run (recorded by RunTests, not by the model): tests/test_calc.py exited 1" in text
    assert text.removesuffix("\n</system-reminder>").endswith("E   assert 1 == 2")
    assert "x" * 2001 not in text  # the tail, bounded by the preview size
    assert attachment_of(msg) == {"type": "tdd_state", "phase": "GREEN", "red_confirmed": True,
                                  "green_passed": False, "afterCompaction": True}


def test_a_post_compaction_announcement_counts_for_dedup():
    msg = create_tdd_state_attachment_message(PhaseLedger(), None, after_compaction=True)
    assert [a for a in run(get_attachments(ctx_for(), [msg])) if a.type == "tdd_state"] == []


def test_post_compaction_tdd_state_without_a_run():
    msg = create_tdd_state_attachment_message(PhaseLedger(), None, after_compaction=True)
    assert "No RunTests run has been observed yet." in str(msg.content)
    plain = create_tdd_state_attachment_message(PhaseLedger(), None, after_compaction=False)
    assert "RunTests run" not in str(plain.content).replace("written only by RunTests from observed test runs", "")


def test_snippet_hunks_numbering_and_truncation():
    from app.loop.attachments.changed_files import (
        DIFF_SNIPPET_MAX_BYTES,
        get_snippet_for_two_file_diff,
    )

    before = "\n".join(f"line {i}" for i in range(1, 41))
    after = before.replace("line 2\n", "line 2 changed\n").replace("line 39", "line 39 changed")
    snippet = get_snippet_for_two_file_diff(before, after)
    first, second = snippet.split("\n...\n")
    assert first.splitlines()[0] == "1\tline 1"
    assert "2\tline 2 changed" in first
    assert second.splitlines()[-1] == "40\tline 40"
    assert get_snippet_for_two_file_diff("same", "same") == ""
    assert DIFF_SNIPPET_MAX_BYTES == 8192
    big = get_snippet_for_two_file_diff("", "\n".join("x" * 100 for _ in range(200)))
    assert big.endswith(" lines truncated] ...")
    assert len(big.split("\n\n... [")[0]) <= DIFF_SNIPPET_MAX_BYTES
