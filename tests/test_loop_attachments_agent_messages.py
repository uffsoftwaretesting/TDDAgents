"""Phase 8: `getAgentPendingMessageAttachments` (`utils/attachments.ts`) and its API text."""

from __future__ import annotations

from app.state.app_state_store import AppStateStore
from app.tasks.local_agent_task.local_agent_task import (
    queue_pending_message,
    register_async_agent,
)
from app.tool import tool_context_for
from app.utils.attachments import (
    Attachment,
    get_agent_pending_message_attachments,
    get_attachments,
    get_critical_system_reminder_attachment,
    normalize_attachment_for_api,
    wrap_command_text,
)


def _context(store: AppStateStore, agent_id: str | None):
    return tool_context_for(store, agent_id=agent_id)


def _store() -> AppStateStore:
    store = AppStateStore()
    register_async_agent(agent_id="a1", description="d", prompt="p", agent_type="Explore",
                         set_app_state=store.update, output_file="/o")
    queue_pending_message("a1", "first", store.update)
    queue_pending_message("a1", "second", store.update)
    return store


def test_the_main_thread_drains_nothing():
    store = _store()
    assert get_agent_pending_message_attachments([], _context(store, None)) == []
    assert store.get().tasks["a1"].pending_messages == ("first", "second")


def test_a_worker_drains_its_own_messages_in_order_as_coordinator_commands():
    store = _store()
    attachments = get_agent_pending_message_attachments([], _context(store, "a1"))
    assert attachments == [
        Attachment("queued_command", {"prompt": "first", "origin": {"kind": "coordinator"}, "isMeta": True}),
        Attachment("queued_command", {"prompt": "second", "origin": {"kind": "coordinator"}, "isMeta": True}),
    ]
    assert store.get().tasks["a1"].pending_messages == ()
    assert get_agent_pending_message_attachments([], _context(store, "a1")) == []


def test_a_worker_without_a_task_drains_nothing():
    assert get_agent_pending_message_attachments([], _context(_store(), "a2")) == []


def test_the_coordinator_origin_wraps_with_upstreams_text():
    assert wrap_command_text("hi", "coordinator") == (
        "The coordinator sent a message while you were working:\nhi\n\n"
        "Address this before completing your current task."
    )


def test_an_explicit_origin_wins_over_the_command_mode():
    texts = normalize_attachment_for_api(
        Attachment("queued_command", {"prompt": "go", "origin": {"kind": "coordinator"},
                                      "commandMode": "task-notification"})
    )
    assert texts == [wrap_command_text("go", "coordinator")]


def test_the_critical_reminder_is_attached_at_every_turn():
    """`getCriticalSystemReminderAttachment` and its `normalizeAttachmentForAPI` case."""
    import asyncio
    import dataclasses

    store = AppStateStore()
    context = dataclasses.replace(_context(store, "a1"), critical_system_reminder="CRITICAL: verify only.")
    attachment = Attachment("critical_system_reminder", {"content": "CRITICAL: verify only."})
    assert get_critical_system_reminder_attachment([], context) == [attachment]
    assert get_critical_system_reminder_attachment([], _context(store, "a1")) == []
    assert normalize_attachment_for_api(attachment) == ["CRITICAL: verify only."]
    for _ in range(2):
        assert attachment in asyncio.run(get_attachments(context, []))
