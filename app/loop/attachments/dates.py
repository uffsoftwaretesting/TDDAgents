"""
The `date_change` source.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getDateChangeAttachments` and
`src/constants/common.ts` -> `getLocalISODate`. Upstream keeps the last emitted date in
session state (`getLastEmittedDate`); a loop run is a session here, so it is kept per
`run_id`. The first sight of a date only records it — the run's opening context already
states today's date.
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Sequence
from typing import Any

from app.loop.attachments.types import Attachment
from app.loop.messages import Message

#: Upstream's `CLAUDE_CODE_OVERRIDE_DATE`, renamed.
OVERRIDE_DATE_ENV = "TDDAGENTS_OVERRIDE_DATE"

_last_emitted: dict[str, str] = {}


def get_local_iso_date() -> str:
    """`getLocalISODate`: today in local time, `YYYY-MM-DD`, unless overridden."""
    override = os.environ.get(OVERRIDE_DATE_ENV)
    if override:
        return override
    return datetime.date.today().isoformat()


def reset_last_emitted_dates() -> None:
    """Forget every run's last emitted date (tests)."""
    _last_emitted.clear()


def get_date_change_attachments(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getDateChangeAttachments`."""
    key = str(getattr(context, "run_id", "") or "")
    current = get_local_iso_date()
    last = _last_emitted.get(key)
    if last is None:
        _last_emitted[key] = current
        return []
    if current == last:
        return []
    _last_emitted[key] = current
    return [Attachment("date_change", {"newDate": current})]


__all__ = ["OVERRIDE_DATE_ENV", "get_date_change_attachments", "get_local_iso_date", "reset_last_emitted_dates"]
