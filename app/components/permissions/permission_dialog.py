"""
A permission dialog as the REPL prints it.

Ported from `reference/claude-code/src/components/permissions/PermissionDialog.tsx` (title,
subtitle, content) and `PermissionPrompt.tsx` (the question, `Do you want to proceed?` unless the
dialog names its own). Each `*_permission_request` module fills one of these the way its
upstream component lays out its Ink tree; colours, borders and dim text are a terminal's
business, so the dialog keeps only the text and its order.
"""

from __future__ import annotations

from dataclasses import dataclass

#: `PermissionPrompt.tsx`: the question when a dialog names none.
DEFAULT_QUESTION = "Do you want to proceed?"


@dataclass(frozen=True, slots=True)
class PermissionDialog:
    """The dialog's parts, top to bottom (each omitted when empty)."""

    title: str
    subtitle: str = ""
    body: str = ""
    description: str = ""
    explanation: str = ""
    question: str = DEFAULT_QUESTION

    def lines(self) -> list[str]:
        """Title, subtitle, body, description, explanation, question — the non-empty ones."""
        parts = (self.title, self.subtitle, self.body, self.description, self.explanation, self.question)
        return [part for part in parts if part]
