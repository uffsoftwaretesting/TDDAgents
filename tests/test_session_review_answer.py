"""
Answering the session's reviews at the terminal (`app/session/driver.py`): what each review
shows, `/edit` in the user's editor (upstream's ctrl-g at the plan approval), and the loop both
the entry point and the REPL read answers with.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from app.session.driver import (
    EDIT_COMMAND,
    EDITABLE_REVIEWS,
    edit_review,
    read_review_answer,
    review_text,
)
from app.utils.prompt_editor import EditorResult

SPEC = {"type": "specification_approval", "prompt": "# Spec"}
PLAN = {"type": "plan_approval", "prompt": "▶ 1. Setup\n· 2. Core", "plan": ["Setup", "Core"], "error": ""}


def test_the_constants():
    assert EDIT_COMMAND == "/edit"
    assert EDITABLE_REVIEWS == {"specification_approval": "specification", "plan_approval": "plan"}


def test_the_specification_review_text():
    assert review_text(SPEC) == (
        "\n🛠️  [Engineer] Technical Specification:\n# Spec\n\n"
        "💡 TIP: Review the specification.\n"
        "   - Type '/yes' to proceed to Planning.\n"
        "   - Type '/edit' to change it in your editor.\n"
        "   - Or type the changes you'd like the Engineer to make."
    )


def test_the_plan_review_text_and_a_failed_command():
    tips = [
        "💡 TIP: Review the plan.",
        "   - Type '/yes' to proceed to Development.",
        ("   - Type '/plan add <text>', '/plan edit <id> <text>', '/plan remove <id>' or "
         "'/plan move <id> <position>' to change an item."),
        "   - Type '/edit' to change it in your editor, one item per line.",
        "   - Or type the changes you'd like the Planner to make.",
    ]
    assert review_text(PLAN) == "\n".join(["\n🗺️  [Planner] TDD Plan:\n▶ 1. Setup\n· 2. Core\n", *tips])
    failed = review_text({**PLAN, "error": "Usage: /plan add <text>"})
    assert failed == "\n".join(["\n🗺️  [Planner] TDD Plan:\n▶ 1. Setup\n· 2. Core\n",
                                "⚠ Usage: /plan add <text>\n", *tips])


def test_the_analyst_review_text_is_unchanged():
    assert review_text({"prompt": "Which?"}) == (
        "\n🤖 [Requirements Analyst]:\nWhich?\n\n💡 TIP: The Analyst needs more details.\n"
        "   - Answer the question to help define the scope.")
    assert "Type '/yes' to proceed to Engineering." in review_text({"prompt": "C", "has_checklist": True})


# ── /edit ───────────────────────────────────────────────────────────────────


class Editor:
    """A fake `edit_file_in_editor`: records what the draft held, then writes `new`."""

    def __init__(self, new: str | None, error: str | None = None) -> None:
        self.new, self.error = new, error
        self.drafts: list[tuple[Path, str]] = []

    def __call__(self, path: Path) -> EditorResult:
        self.drafts.append((path, path.read_text(encoding="utf-8")))
        if self.new is None:
            return EditorResult(None, self.error)
        path.write_text(self.new, encoding="utf-8")
        return EditorResult(self.new)


def test_the_specification_is_edited_in_a_draft_that_is_then_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_TMPDIR", str(tmp_path))
    editor = Editor("# Edited\n")
    assert edit_review(SPEC, editor) == ({"edited": "# Edited\n"}, "")
    ((path, draft),) = editor.drafts
    assert draft == "# Spec\n"
    assert path.parent == tmp_path / f"tddagents-{os.getuid()}"
    assert path.name.startswith("specification-") and path.suffix == ".md"
    assert not path.exists()


def test_the_plan_is_edited_one_item_per_line(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_TMPDIR", str(tmp_path))
    editor = Editor("Setup\nCore\nDocker\n")
    assert edit_review(PLAN, editor) == ({"edited": "Setup\nCore\nDocker\n"}, "")
    assert editor.drafts[0][1] == "Setup\nCore\n"
    assert editor.drafts[0][0].name.startswith("plan-")


@pytest.mark.parametrize(("editor", "message"), [
    (Editor(None), "No editor found: set $VISUAL or $EDITOR."),
    (Editor(None, "vi exited with code 1"), "vi exited with code 1"),
    (Editor("# Spec\n\n"), "The specification is unchanged."),
])
def test_an_edit_that_produced_nothing_says_why(tmp_path, monkeypatch, editor: Editor, message: str):
    monkeypatch.setenv("TDDAGENTS_TMPDIR", str(tmp_path))
    assert edit_review(SPEC, editor) == (None, message)
    assert not editor.drafts[0][0].exists()


def test_only_the_specification_and_the_plan_are_editable():
    assert edit_review({"type": "checklist_approval"}, Editor("x")) == (
        None, "/edit works on the specification and on the plan.")


# ── the answer loop ─────────────────────────────────────────────────────────


def _answer(payload: dict[str, Any], lines: list[str | None], edit: Any = None) -> tuple[Any, list[str]]:
    shown: list[str] = []
    replies = iter(lines)
    kwargs = {"edit": edit} if edit is not None else {}
    return read_review_answer(payload, lambda: next(replies), shown.append, **kwargs), shown


def test_the_answer_is_the_users_text():
    answer, shown = _answer(SPEC, ["  /yes  "])
    assert answer == "/yes"
    assert shown == [review_text(SPEC)]


@pytest.mark.parametrize("line", [None, "/exit", " /QUIT "])
def test_leaving_returns_none(line: str | None):
    assert _answer(SPEC, [line])[0] is None


def test_an_edit_is_the_answer():
    answer, _ = _answer(PLAN, ["/edit"], edit=lambda payload: ({"edited": "x"}, ""))
    assert answer == {"edited": "x"}


def test_a_failed_edit_says_why_and_asks_again():
    answer, shown = _answer(SPEC, ["/edit", "add a CLI"], edit=lambda payload: (None, "No editor found."))
    assert answer == "add a CLI"
    assert shown == [review_text(SPEC), "No editor found."]


def test_slash_edit_at_the_analyst_is_ordinary_text():
    def never(payload: Any) -> Any:
        raise AssertionError("no editor at the analyst")

    assert _answer({"type": "clarification", "prompt": "Q"}, ["/edit"], edit=never)[0] == "/edit"
