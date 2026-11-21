"""
What counts as the user's input for the interaction clock.

Ported from `reference/claude-code/src/ink/components/App.tsx` -> `processKeysInBatch`: one batch of keys marks
the clock once when it holds a user key or a mouse event. A terminal response (upstream's `kind: 'response'`;
here prompt_toolkit's `Keys.CPRResponse`, the reply to its cursor-position query) is not input, and neither is
mode-1003 motion without a button (`(button & 0x20) && (button & 0x03) === 3`): passive cursor drift is not
engagement. The prompt_toolkit seam (`app/screens/terminal_io.py`) feeds every batch through here.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from prompt_toolkit.keys import Keys

from app.bootstrap.state import update_last_interaction_time
from app.ink.parse_keypress import parse_mouse_event


def is_user_input(key_press: Any) -> bool:
    """A key the user pressed, or a mouse event other than motion with no button held."""
    key = getattr(key_press, "key", None)
    if key == Keys.CPRResponse:
        return False
    if key == Keys.Vt100MouseEvent:
        mouse = parse_mouse_event(str(getattr(key_press, "data", "")))
        if mouse is not None and mouse.button & 0x20 and mouse.button & 0x03 == 3:
            return False
    return True


def process_keys_in_batch(key_presses: Iterable[Any]) -> None:
    """`processKeysInBatch`'s clock update: mark it once if any key of the batch is the user's."""
    if any(is_user_input(key_press) for key_press in key_presses):
        update_last_interaction_time()


__all__ = ["is_user_input", "process_keys_in_batch"]
