"""
`AskUserQuestion`: the model asks the user multiple-choice questions and goes on with the answers.

Ported from `reference/claude-code/src/tools/AskUserQuestionTool/AskUserQuestionTool.tsx`
(`AskUserQuestionTool`, its schemas, `UNIQUENESS_REFINE`, `mapToolResultToToolResultBlockParam`)
and `prompt.ts` (`ASK_USER_QUESTION_TOOL_PROMPT`, `DESCRIPTION`). The answers are collected by
the permission prompt, as upstream's `AskUserQuestionPermissionRequest` does: `checkPermissions`
always asks and `requiresUserInteraction` keeps that ask under every mode, so the REPL's
`canUseTool` shows the questions and allows the call with the user's `answers` in its input.
A run with nobody to ask (headless, a worker) denies it, as upstream's `-p` does; the TDD stop
hook then lets that turn end (`awaiting_user_answer`), as upstream's `-p` ends once the model
stops, and the user's answer is the resumed session's next prompt.

Divergences: no `preview` guidance in the prompt (upstream adds it only for an SDK caller that
set a preview format, which this build has none of), so the HTML preview check is not ported;
the prompt's "Plan mode note" becomes a TDD plan note (user decision: no "Should I proceed?" between
sub-requirements, as upstream's note forbids it before a plan's approval); the uniqueness check runs in
`validate_input`, since the schema is JSON Schema, not zod.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.tool import BuiltTool, ToolContext, ToolResult, ValidationResult, build_tool
from app.types.permissions import PermissionBehavior, PermissionResult
from app.utils.frontmatter_parser import parse_markdown_frontmatter

ASK_USER_QUESTION_TOOL_NAME = "AskUserQuestion"
ASK_USER_QUESTION_TOOL_CHIP_WIDTH = 12

PROMPT_PATH = Path(__file__).with_name("prompt.md")

UNIQUENESS_MESSAGE = "Question texts must be unique, option labels must be unique within each question"

_OPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": (
            "The display text for this option that the user will see and select. Should be concise "
            "(1-5 words) and clearly describe the choice.")},
        "description": {"type": "string", "description": (
            "Explanation of what this option means or what will happen if chosen. Useful for providing "
            "context about trade-offs or implications.")},
        "preview": {"type": "string", "description": (
            "Optional preview content rendered when this option is focused. Use for mockups, code snippets, "
            "or visual comparisons that help users compare options. See the tool description for the "
            "expected content format.")},
    },
    "required": ["label", "description"],
}

_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": (
            "The complete question to ask the user. Should be clear, specific, and end with a question mark. "
            "Example: \"Which library should we use for date formatting?\" If multiSelect is true, phrase it "
            "accordingly, e.g. \"Which features do you want to enable?\"")},
        "header": {"type": "string", "description": (
            f"Very short label displayed as a chip/tag (max {ASK_USER_QUESTION_TOOL_CHIP_WIDTH} chars). "
            "Examples: \"Auth method\", \"Library\", \"Approach\".")},
        "options": {"type": "array", "items": _OPTION_SCHEMA, "minItems": 2, "maxItems": 4, "description": (
            "The available choices for this question. Must have 2-4 options. Each option should be a distinct, "
            "mutually exclusive choice (unless multiSelect is enabled). There should be no 'Other' option, "
            "that will be provided automatically.")},
        "multiSelect": {"type": "boolean", "default": False, "description": (
            "Set to true to allow the user to select multiple options instead of just one. Use when choices "
            "are not mutually exclusive.")},
    },
    "required": ["question", "header", "options"],
}

ASK_USER_QUESTION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {"type": "array", "items": _QUESTION_SCHEMA, "minItems": 1, "maxItems": 4,
                      "description": "Questions to ask the user (1-4 questions)"},
        "answers": {"type": "object", "additionalProperties": {"type": "string"},
                    "description": "User answers collected by the permission component"},
        "annotations": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "preview": {"type": "string", "description": (
                        "The preview content of the selected option, if the question used previews.")},
                    "notes": {"type": "string", "description": "Free-text notes the user added to their selection."},
                },
            },
            "description": ("Optional per-question annotations from the user (e.g., notes on preview selections). "
                            "Keyed by question text."),
        },
        "metadata": {
            "type": "object",
            "properties": {"source": {"type": "string", "description": (
                "Optional identifier for the source of this question (e.g., \"remember\" for /remember command). "
                "Used for analytics tracking.")}},
            "description": "Optional metadata for tracking and analytics purposes. Not displayed to user.",
        },
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def questions_are_unique(questions: Sequence[Mapping[str, Any]]) -> bool:
    """`UNIQUENESS_REFINE.check`: distinct question texts, and distinct labels within each question."""
    texts = [q.get("question") for q in questions]
    if len(texts) != len(set(texts)):
        return False
    for question in questions:
        labels = [o.get("label") for o in question.get("options") or []]
        if len(labels) != len(set(labels)):
            return False
    return True


def answers_text(answers: Mapping[str, str], annotations: Mapping[str, Mapping[str, str]] | None) -> str:
    """`mapToolResultToToolResultBlockParam`: what the model is told the user answered."""
    parts = []
    for question, answer in answers.items():
        annotation = (annotations or {}).get(question) or {}
        piece = [f'"{question}"="{answer}"']
        if annotation.get("preview"):
            piece.append(f"selected preview:\n{annotation['preview']}")
        if annotation.get("notes"):
            piece.append(f"user notes: {annotation['notes']}")
        parts.append(" ".join(piece))
    return (f"User has answered your questions: {', '.join(parts)}. "
            "You can now continue with the user's answers in mind.")


def build_ask_user_question_tool() -> BuiltTool:
    def validate(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
        if not questions_are_unique(input_args.get("questions") or []):
            return ValidationResult(valid=False, message=UNIQUENESS_MESSAGE, error_code=1)
        return ValidationResult(valid=True)

    def check(input_args: dict[str, Any], context: ToolContext) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ASK, message="Answer questions?",
                                updated_input=input_args)

    async def call(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(content=answers_text(input_args.get("answers") or {}, input_args.get("annotations")))

    return build_tool(
        name=ASK_USER_QUESTION_TOOL_NAME,
        prompt=parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1],
        input_schema=ASK_USER_QUESTION_INPUT_SCHEMA,
        description=lambda args: f"Ask the user {len(args.get('questions') or [])} question(s)",
        is_concurrency_safe=lambda args: True,
        is_read_only=lambda args: True,
        requires_user_interaction=lambda: True,
        validate_input=validate,
        check_permissions=check,
        call=call,
        max_result_size_chars=100_000,
    )


def awaiting_user_answer(messages: Sequence[BaseMessage]) -> tuple[dict[str, Any], ...] | None:
    """
    The questions the model asked since the user last spoke that nobody could answer (an
    `AskUserQuestion` call the gate denied), or `None`. A run with nobody to ask stops there and
    waits, as upstream's `-p` ends when the model stops after the denial. A call refused for
    its input is not a question asked, and a user's rejection is an answer.
    """
    asked: dict[str, tuple[dict[str, Any], ...]] = {}
    unanswered: tuple[dict[str, Any], ...] | None = None
    for message in messages:
        if isinstance(message, HumanMessage) and not message.additional_kwargs.get("is_meta"):
            asked, unanswered = {}, None
        elif isinstance(message, ToolMessage):
            questions = asked.get(message.tool_call_id)
            if questions is not None and message.additional_kwargs.get("permission_behavior") == "deny":
                unanswered = questions
        elif isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call.get("name") == ASK_USER_QUESTION_TOOL_NAME:
                    asked[str(call.get("id"))] = tuple((call.get("args") or {}).get("questions") or ())
    return unanswered


def render_questions(questions: Sequence[Mapping[str, Any]]) -> str:
    """The questions as text, for a report of a session that waits for the user."""
    lines = []
    for question in questions:
        lines.append(f"[{question.get('header', '')}] {question.get('question', '')}")
        for index, option in enumerate(question.get("options") or [], start=1):
            lines.append(f"  {index}. {option.get('label', '')} — {option.get('description', '')}")
    return "\n".join(lines)


__all__ = [
    "ASK_USER_QUESTION_INPUT_SCHEMA",
    "ASK_USER_QUESTION_TOOL_NAME",
    "UNIQUENESS_MESSAGE",
    "answers_text",
    "awaiting_user_answer",
    "build_ask_user_question_tool",
    "questions_are_unique",
    "render_questions",
]
