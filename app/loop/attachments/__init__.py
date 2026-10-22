"""
Per-turn attachments (Phase 5): upstream's `getAttachments` pipeline and the sources this
build has subsystems for, plus TDDAgents' `tdd_state`. See `pipeline.py` for the order and the
sources that are not ported.
"""

from __future__ import annotations

from app.loop.attachments.pipeline import get_attachment_messages, get_attachments
from app.loop.attachments.reminders import (
    AUTO_MODE_ATTACHMENT_CONFIG,
    TODO_REMINDER_CONFIG,
)
from app.loop.attachments.tdd_state import create_tdd_state_attachment_message
from app.loop.attachments.types import (
    Attachment,
    attachment_of,
    create_attachment_messages,
    normalize_attachment_for_api,
)

__all__ = [
    "AUTO_MODE_ATTACHMENT_CONFIG",
    "TODO_REMINDER_CONFIG",
    "Attachment",
    "attachment_of",
    "create_attachment_messages",
    "create_tdd_state_attachment_message",
    "get_attachment_messages",
    "get_attachments",
    "normalize_attachment_for_api",
]
