"""Hook attachments and the messages the model sees (`app/utils/messages.py`)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.utils.messages import (
    ATTACHMENT_KEY,
    HookAttachment,
    HookBlockingError,
    async_hook_response_messages,
    attachment_type_of,
    create_attachment_message,
    is_attachment_message,
    is_hook_stopped_continuation,
    messages_for,
    normalize_attachment_for_api,
    reorder_attachments_for_api,
)


def att(kind: str, event: str = "PreToolUse", **kw) -> HookAttachment:
    return HookAttachment(type=kind, hook_name=f"{event}:Bash", tool_use_id="t1", hook_event=event, **kw)


def test_attachment_key():
    assert ATTACHMENT_KEY == "attachment"


def test_as_dict_minimal_and_full():
    assert att("hook_cancelled").as_dict() == {
        "type": "hook_cancelled", "hookName": "PreToolUse:Bash", "toolUseID": "t1", "hookEvent": "PreToolUse"}
    full = att("hook_success", content=("a", "b"), message="m", stdout="o", stderr="e", exit_code=0,
               command="c", duration_ms=5,
               blocking_error=HookBlockingError(blocking_error="b", command="cmd"))
    assert full.as_dict() == {
        "type": "hook_success", "hookName": "PreToolUse:Bash", "toolUseID": "t1", "hookEvent": "PreToolUse",
        "content": ["a", "b"], "message": "m", "stdout": "o", "stderr": "e", "exitCode": 0, "command": "c",
        "durationMs": 5, "blockingError": {"blockingError": "b", "command": "cmd"},
    }
    assert att("x", content="s").as_dict()["content"] == "s"


def test_blocking_error_text():
    a = att("hook_blocking_error", blocking_error=HookBlockingError(blocking_error="nope", command="vet.sh"))
    assert normalize_attachment_for_api(a) == 'PreToolUse:Bash hook blocking error from command: "vet.sh": nope'
    assert normalize_attachment_for_api(att("hook_blocking_error")) is None


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit"])
def test_success_visible_only_for_session_start_and_prompt_submit(event):
    assert normalize_attachment_for_api(att("hook_success", event, content="hi")) == f"{event}:Bash hook success: hi"
    assert normalize_attachment_for_api(att("hook_success", event, content="")) is None
    assert normalize_attachment_for_api(att("hook_success", event)) is None


def test_success_hidden_for_other_events():
    assert normalize_attachment_for_api(att("hook_success", "PreToolUse", content="hi")) is None
    assert normalize_attachment_for_api(att("hook_success", "Stop", content="hi")) is None


def test_additional_context():
    assert normalize_attachment_for_api(att("hook_additional_context", content=("a", "b"))) == (
        "PreToolUse:Bash hook additional context: a\nb")
    assert normalize_attachment_for_api(att("hook_additional_context", content="one")) == (
        "PreToolUse:Bash hook additional context: one")
    assert normalize_attachment_for_api(att("hook_additional_context", content=())) is None
    assert normalize_attachment_for_api(att("hook_additional_context", content="")) is None
    assert normalize_attachment_for_api(att("hook_additional_context")) is None


def test_stopped_continuation():
    assert normalize_attachment_for_api(att("hook_stopped_continuation", message="halt")) == (
        "PreToolUse:Bash hook stopped continuation: halt")


@pytest.mark.parametrize("kind", ["hook_cancelled", "hook_non_blocking_error", "hook_error_during_execution",
                                  "hook_system_message", "hook_permission_decision", "other"])
def test_hidden_types(kind):
    assert normalize_attachment_for_api(att(kind, content="x", message="m")) is None


def test_create_attachment_message():
    a = att("hook_stopped_continuation", message="halt")
    m = create_attachment_message(a)
    assert isinstance(m, HumanMessage)
    assert m.content == "<system-reminder>\nPreToolUse:Bash hook stopped continuation: halt\n</system-reminder>"
    assert m.additional_kwargs == {"is_meta": True, "attachment": a.as_dict()}


def test_hidden_attachment_is_logged(caplog):
    with caplog.at_level(logging.INFO, logger="TDDOrchestrator.Hooks"):
        assert create_attachment_message(att("hook_cancelled")) is None
    assert caplog.records[0].getMessage().startswith("Hook attachment hook_cancelled (PreToolUse:Bash): ")


def test_attachment_type_of():
    assert attachment_type_of(create_attachment_message(att("hook_stopped_continuation", message="m"))) == (
        "hook_stopped_continuation")
    assert attachment_type_of(HumanMessage(content="x")) is None
    assert attachment_type_of(SimpleNamespace()) is None
    assert attachment_type_of(SimpleNamespace(additional_kwargs="x")) is None
    assert attachment_type_of(HumanMessage(content="", additional_kwargs={"attachment": "x"})) is None
    assert attachment_type_of(HumanMessage(content="", additional_kwargs={"attachment": {"type": 1}})) is None
    assert attachment_type_of(HumanMessage(content="", additional_kwargs={"attachment": {}})) is None


def test_is_hook_stopped_continuation():
    assert is_hook_stopped_continuation(create_attachment_message(att("hook_stopped_continuation", message="m")))
    assert not is_hook_stopped_continuation(create_attachment_message(att("hook_additional_context", content="c")))
    assert not is_hook_stopped_continuation(ToolMessage(content="", tool_call_id="c"))


def test_is_attachment_message():
    assert is_attachment_message(HumanMessage(content="", additional_kwargs={"attachment": {}})) is True
    assert is_attachment_message(HumanMessage(content="", additional_kwargs={"attachment": "x"})) is False
    assert is_attachment_message(HumanMessage(content="")) is False
    assert is_attachment_message(SimpleNamespace()) is False


def test_async_hook_response_messages():
    meta = {"type": "async_hook_response", "processId": "p"}
    msgs = async_hook_response_messages(
        {"systemMessage": "sys", "hookSpecificOutput": {"additionalContext": "ctx"}}, meta)
    assert [m.content for m in msgs] == ["<system-reminder>\nsys\n</system-reminder>",
                                         "<system-reminder>\nctx\n</system-reminder>"]
    assert all(m.additional_kwargs == {"is_meta": True, "attachment": meta} for m in msgs)
    assert msgs[0].additional_kwargs["attachment"] is not meta
    assert async_hook_response_messages({}, meta) == ()
    assert async_hook_response_messages({"systemMessage": "", "hookSpecificOutput": "x"}, meta) == ()
    assert async_hook_response_messages({"systemMessage": 1, "hookSpecificOutput": {"additionalContext": 2}},
                                        meta) == ()
    only_ctx = async_hook_response_messages({"hookSpecificOutput": {"additionalContext": "c"}}, meta)
    assert [m.content for m in only_ctx] == ["<system-reminder>\nc\n</system-reminder>"]


def test_messages_for_drops_hidden_ones():
    out = messages_for([att("hook_cancelled"), att("hook_stopped_continuation", message="a"),
                        att("hook_additional_context", content="b")])
    assert [m.content for m in out] == [
        "<system-reminder>\nPreToolUse:Bash hook stopped continuation: a\n</system-reminder>",
        "<system-reminder>\nPreToolUse:Bash hook additional context: b\n</system-reminder>",
    ]
    assert messages_for([]) == ()


def _attachment(text: str) -> HumanMessage:
    return HumanMessage(content=text, additional_kwargs={"attachment": {"type": "hook_additional_context"}})


def test_reorder_moves_attachments_after_the_result_block():
    a = AIMessage(content="", tool_calls=[{"name": "X", "args": {}, "id": "1"}, {"name": "X", "args": {}, "id": "2"}])
    r1, r2 = ToolMessage(content="r1", tool_call_id="1"), ToolMessage(content="r2", tool_call_id="2")
    h1, h2 = _attachment("h1"), _attachment("h2")
    out = reorder_attachments_for_api([a, r1, h1, r2, h2])
    assert out == [a, r1, r2, h1, h2]


def test_reorder_leaves_other_messages_untouched():
    u = HumanMessage(content="task")
    h = _attachment("before any tool")
    plain = HumanMessage(content="plain user")
    r = ToolMessage(content="r", tool_call_id="1")
    msgs = [u, h, AIMessage(content="x"), r, plain, _attachment("after user")]
    assert reorder_attachments_for_api(msgs) == msgs


def test_reorder_stops_the_block_at_a_non_attachment():
    r1 = ToolMessage(content="r1", tool_call_id="1")
    h = _attachment("h")
    plain = HumanMessage(content="u")
    r2 = ToolMessage(content="r2", tool_call_id="2")
    assert reorder_attachments_for_api([r1, h, plain, r2]) == [r1, h, plain, r2]
    assert reorder_attachments_for_api([h, r1]) == [h, r1]
    assert reorder_attachments_for_api([]) == []
    out = reorder_attachments_for_api((r1, h, r2))
    assert isinstance(out, list) and out == [r1, r2, h]
