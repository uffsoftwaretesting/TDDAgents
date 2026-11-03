"""
`AskUserQuestion` option previews in the REPL (upstream's `AskUserQuestionPermissionRequest/`
`PreviewBox.tsx`, `QuestionView.tsx`'s `hasAnyPreview`, and the submit handler's `annotations`).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.components.permissions.ask_user_question_permission_request.preview_box import (
    DEFAULT_MAX_LINES,
    DEFAULT_MIN_WIDTH,
    has_any_preview,
    preview_box,
)
from app.hooks.use_can_use_tool import selected_preview_annotations
from app.screens.repl import PREVIEW_INDENT, render_question
from app.tools.ask_user_question_tool.ask_user_question_tool import answers_text
from tests.test_ask_user_question_tool import interactive, run_call

QUESTION_TEXT = "Which layout should the report use?"
TABLE_PREVIEW = "| test | result |\n| a    | pass   |"
LAYOUT: dict[str, Any] = {
    "question": QUESTION_TEXT,
    "header": "Layout",
    "options": [
        {"label": "Table", "description": "one row per test", "preview": TABLE_PREVIEW},
        {"label": "List", "description": "one line per test"},
    ],
}


class TestPreviewBox:
    def test_the_box_is_at_least_min_width_and_pads_each_line(self):
        assert preview_box("ab\nc", min_width=4, max_width=80) == [
            "┌──────┐",
            "│ ab   │",
            "│ c    │",
            "└──────┘",
        ]

    def test_upstreams_defaults(self):
        assert (DEFAULT_MAX_LINES, DEFAULT_MIN_WIDTH) == (20, 40)
        box = preview_box("x", max_width=200)
        assert len(box[0]) == DEFAULT_MIN_WIDTH + 4 and box[1] == "│ x" + " " * (DEFAULT_MIN_WIDTH - 1) + " │"

    def test_the_widest_line_sets_the_width_up_to_max_width(self):
        assert preview_box("abcdef", min_width=2, max_width=80)[1] == "│ abcdef │"
        assert preview_box("abcdefgh", min_width=2, max_width=8) == ["┌──────┐", "│ abcd │", "└──────┘"]

    def test_lines_past_max_lines_are_hidden_behind_the_bar(self):
        box = preview_box("1\n2\n3\n4", max_lines=2, min_width=30, max_width=80)
        assert box[1:3] == ["│ 1" + " " * 29 + " │", "│ 2" + " " * 29 + " │"]
        assert box[3] == "├─── ✂ ─── 2 lines hidden " + "─" * (34 - 2 - 25) + "┤" and len(box[3]) == len(box[0]) == 34
        assert box[4].startswith("└") and len(box) == 5

    def test_twenty_lines_by_default(self):
        box = preview_box("\n".join(str(i) for i in range(22)), max_width=200)
        assert len(box) == 1 + 20 + 1 + 1 and "2 lines hidden" in box[-2]
        assert len(preview_box("\n".join("x" * 20), max_width=200)) == 22

    def test_the_terminal_width_bounds_it_by_default(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "30")
        assert len(preview_box("x" * 100)[0]) == 26


@pytest.mark.parametrize("question, shown", [
    (LAYOUT, True),
    ({**LAYOUT, "multiSelect": True}, False),
    ({**LAYOUT, "options": [{"label": "a", "description": "b"}]}, False),
    ({**LAYOUT, "options": [{"label": "a", "description": "b", "preview": ""}]}, False),
])
def test_has_any_preview(question: dict[str, Any], shown: bool):
    assert has_any_preview(question) is shown


class TestRenderQuestion:
    def test_each_preview_is_boxed_under_its_option(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "120")
        box = [PREVIEW_INDENT + line for line in preview_box(TABLE_PREVIEW, max_width=111)]
        assert render_question(LAYOUT, 1, 1).splitlines() == [
            "? [Layout] Which layout should the report use?",
            "  1. Table — one row per test",
            *box,
            "  2. List — one line per test",
            "  3. Other — type your own answer",
            "Choose 1-2, or type your own answer.",
        ]
        assert box[1] == PREVIEW_INDENT + "│ | test | result |" + " " * (DEFAULT_MIN_WIDTH - 17) + " │"

    def test_the_box_fits_the_terminal_after_the_indent(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "40")
        wide = {**LAYOUT, "options": [{"label": "a", "description": "b", "preview": "y" * 100}]}
        lines = render_question(wide, 1, 1).splitlines()
        assert lines[2] == PREVIEW_INDENT + "┌" + "─" * (40 - 4 - len(PREVIEW_INDENT) - 2) + "┐"

    def test_a_multi_select_question_shows_no_preview(self):
        text = render_question({**LAYOUT, "multiSelect": True}, 1, 1)
        assert "┌" not in text and "| test |" not in text


class TestAnnotations:
    def test_the_picked_options_preview(self):
        answers = {QUESTION_TEXT: "Table"}
        assert selected_preview_annotations([LAYOUT], answers) == {
            QUESTION_TEXT: {"preview": TABLE_PREVIEW}}

    @pytest.mark.parametrize("answer", ["List", "my own layout", ""])
    def test_none_without_a_picked_preview(self, answer: str):
        assert selected_preview_annotations([LAYOUT], {QUESTION_TEXT: answer}) == {}
        assert selected_preview_annotations([LAYOUT], {}) == {}

    def test_the_model_hears_the_selected_preview(self):
        answers = {QUESTION_TEXT: "Table"}
        ctx, _ = interactive(answers)
        message = run_call(ctx, {"questions": [LAYOUT]}).message
        assert message.status == "success"
        assert message.content == answers_text(answers, {QUESTION_TEXT: {"preview": TABLE_PREVIEW}})
        assert "selected preview:\n| test | result |" in message.content

    def test_without_a_preview_the_input_has_no_annotations(self):
        answers = {QUESTION_TEXT: "List"}
        ctx, _ = interactive(answers)
        assert run_call(ctx, {"questions": [LAYOUT]}).message.content == answers_text(answers, None)
