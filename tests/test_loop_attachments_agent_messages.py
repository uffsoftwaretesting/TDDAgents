"""Phase 8: `getAgentPendingMessageAttachments` (`utils/attachments.ts`) and its API text."""

from __future__ import annotations

from app.loop.attachments.agent_messages import get_agent_pending_message_attachments
from app.loop.attachments.types import (
    Attachment,
    normalize_attachment_for_api,
    wrap_command_text,
)
from app.loop.context.app_state import AppStateStore
from app.loop.context.tool_context import tool_context_for
from app.loop.tasks.local_agent_task import queue_pending_message, register_async_agent


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
