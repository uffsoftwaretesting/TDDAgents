"""
The message that ends a turn, in an orange ⚠ box (`app/components/messages/assistant_text_message.py`, user
decision 2026-11-23): only on a terminal that shows colour, only for the answer with no tool call that ends the
turn (its stop reason), its Markdown rendered and never read as rich markup.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.components.design_system import theme
from app.components.design_system.rich_render import plain_text
from app.components.messages.assistant_text_message import (
    FINAL_MESSAGE_TITLE,
    is_turn_final_text,
    stop_reason_of,
    style_final_message,
)
from app.components.messages.collapsed_read_search_content import (
    CollapsedReadSearchRenderer,
)
from app.screens.repl import Repl, render_message
from tests.test_repl import FakeIO

ORANGE = "38;2;255;140;0"
SUMMARY = ("The 10 sub-requirements are **done**.\n\n- **Tests:** `1285` passed\n- Coverage [95.6%] :smile:\n\n"
           "## Next\n\nI will check the verdict.")


def final(text: str = SUMMARY, **metadata: Any) -> AIMessage:
    return AIMessage(content=text, response_metadata=metadata or {"stop_reason": "end_turn"}, id="r1")


def renderer(final_text: Any = None) -> CollapsedReadSearchRenderer:
    return CollapsedReadSearchRenderer(lambda: (), lambda: None, render_message, final_text=final_text)


# ── which message ends the turn ─────────────────────────────────────────────────

@pytest.mark.parametrize("metadata", [{"stop_reason": "end_turn"}, {"stop_reason": "stop_sequence"},
                                      {"finish_reason": "stop"}])
def test_an_answer_that_ends_the_turn(metadata):
    assert is_turn_final_text(final(**metadata), SUMMARY)


@pytest.mark.parametrize("message", [
    AIMessage(content="Now I'll run the tests."),  # a block followed by a tool call: no stop reason
    AIMessage(content="cut", response_metadata={"stop_reason": "max_tokens"}),  # resumed, not final
    AIMessage(content="call", response_metadata={"stop_reason": "tool_use"},
              tool_calls=[{"name": "Bash", "args": {}, "id": "c1"}]),
    AIMessage(content="   ", response_metadata={"stop_reason": "end_turn"}),
])
def test_anything_else_is_not_final(message):
    assert not is_turn_final_text(message, str(message.content))


def test_the_stop_reason_is_read_from_the_metadata():
    assert stop_reason_of(final(stop_reason="end_turn")) == "end_turn"
    assert stop_reason_of(final(finish_reason="stop")) == "stop"
    assert stop_reason_of(AIMessage(content="x")) is None
    assert stop_reason_of(SimpleNamespace(response_metadata={"stop_reason": 3})) is None
    assert stop_reason_of(SimpleNamespace()) is None
    assert not is_turn_final_text(SimpleNamespace(tool_calls=[], response_metadata={"stop_reason": "end_turn"}), "x")


# ── the box ────────────────────────────────────────────────────────────────────

def test_the_box_is_orange_titled_full_width_and_renders_markdown():
    ansi = style_final_message(SUMMARY, 70)
    lines = plain_text(ansi).split("\n")
    assert theme.FINAL_MESSAGE == "rgb(255,140,0)" and ORANGE in ansi and "\x1b[1;" + ORANGE in ansi
    assert lines[0].startswith(f"╭─ {FINAL_MESSAGE_TITLE}") and lines[0].endswith("╮")
    assert lines[-1].startswith("╰") and all(len(line) == 70 for line in lines)
    body = "\n".join(lines)
    assert "**" not in body and "The 10 sub-requirements are done." in body  # Markdown, not its source
    assert "• Tests: 1285 passed" in body and "## " not in body and "Next" in body
    assert "[95.6%] :smile:" in body  # rich markup and emoji codes stay as written


def test_a_coloured_renderer_boxes_only_the_final_answer():
    seen: list[str] = []

    def box(text: str) -> str:
        seen.append(text)
        return f"<box {text}>"

    view = renderer(box)
    assert view.feed(AIMessage(content="Now I'll run the tests.", id="r0")) == ["● Now I'll run the tests."]
    assert view.feed(final("All done.")) == ["<box All done.>"]
    assert seen == ["All done."]


def test_a_plain_renderer_keeps_the_dot_row():
    assert renderer().feed(final("All done.")) == ["● All done."]


# ── the REPL chooses by the terminal ───────────────────────────────────────────

class ColourIO(FakeIO):
    supports_color = True


def test_a_coloured_repl_shows_the_final_answer_in_the_orange_box():
    io = ColourIO()
    repl = Repl(SimpleNamespace(on_message=None, ctx=SimpleNamespace()), io)  # type: ignore[arg-type]
    repl._show(AIMessage(content="Running the suite.", id="r0"))
    repl._show(final("**All** done."))
    assert io.out[0] == "● Running the suite."
    assert ORANGE in io.out[1] and plain_text(io.out[1]).startswith(f"╭─ {FINAL_MESSAGE_TITLE}")
    assert "All done." in plain_text(io.out[1])


def test_a_plain_repl_writes_the_dot_row():
    io = FakeIO()
    repl = Repl(SimpleNamespace(on_message=None, ctx=SimpleNamespace()), io)  # type: ignore[arg-type]
    repl._show(final("**All** done."))
    assert io.out == ["● **All** done."]
