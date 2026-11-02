"""
A side question: answered from the conversation in one response, without touching it.

Ported from `reference/claude-code/src/utils/sideQuestion.ts` -> `findBtwTriggerPositions`,
`runSideQuestion` (its wrapper text verbatim) and `extractSideQuestionResponse`. Upstream runs a
cache-sharing fork (`runForkedAgent`, one turn, every tool denied); here, as the compaction's fork
(`app/services/compact/summarizer.py`), it is one model call over the run's state with the wrapped
question appended: the same system prompt and tools, so the request shares the main thread's
prefix, and nothing it says reaches the conversation. The SDK's `side_question` control request
answers with it.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

#: `BTW_PATTERN`.
BTW_PATTERN = re.compile(r"^/btw\b", re.IGNORECASE)

SIDE_QUESTION_WRAPPER = """<system-reminder>This is a side question from the user. You must answer this question directly in a single response.

IMPORTANT CONTEXT:
- You are a separate, lightweight agent spawned to answer this one question
- The main agent is NOT interrupted - it continues working independently in the background
- You share the conversation context but are a completely separate instance
- Do NOT reference being interrupted or what you were "previously doing" - that framing is incorrect

CRITICAL CONSTRAINTS:
- You have NO tools available - you cannot read files, run commands, search, or take any actions
- This is a one-off response - there will be no follow-up turns
- You can ONLY provide information based on what you already know from the conversation context
- NEVER say things like "Let me try...", "I'll now...", "Let me check...", or promise to take any action
- If you don't know the answer, say so - do not offer to look it up or investigate

Simply answer the question with the information you have.</system-reminder>

"""  # noqa: E501

#: `(state, config) -> the model's messages`: `LoopDeps.call_model`.
CallModel = Callable[[Any, Any], AsyncIterator[Any]]


def find_btw_trigger_positions(text: str) -> list[dict[str, Any]]:
    """`findBtwTriggerPositions`: where `/btw` opens the text."""
    return [{"word": m.group(0), "start": m.start(), "end": m.end()} for m in BTW_PATTERN.finditer(text)]


def wrap_side_question(question: str) -> str:
    """The question as the fork receives it."""
    return f"{SIDE_QUESTION_WRAPPER}{question}"


def extract_side_question_response(messages: Sequence[Any]) -> str | None:
    """`extractSideQuestionResponse`: the reply's text, a note when it tried a tool, the API error, or `None`."""
    replies = [m for m in messages if isinstance(m, AIMessage)]
    api_error = next((m for m in replies if m.additional_kwargs.get("is_api_error_message")), None)
    answers = [m for m in replies if m is not api_error]
    if answers:
        texts = []
        for reply in answers:
            content = reply.content
            texts.extend([content] if isinstance(content, str) else [
                str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"])
        text = "\n\n".join(t for t in texts if t).strip()
        if text:
            return text
        call = next((c for m in answers for c in m.tool_calls), None)
        if call is not None:
            return (f"(The model tried to call {call.get('name') or 'a tool'} instead of answering directly. "
                    "Try rephrasing or ask in the main conversation.)")
    if api_error is not None:
        content = api_error.content if isinstance(api_error.content, str) else str(api_error.content)
        return f"(API error: {content})"
    return None


async def run_side_question(question: str, state: Any, config: Any, call_model: CallModel) -> str | None:
    """`runSideQuestion`: one model call over `state`'s conversation with the wrapped question."""
    fork = dataclasses.replace(state, messages=(*state.messages, HumanMessage(content=wrap_side_question(question))))
    return extract_side_question_response([message async for message in call_model(fork, config)])


__all__ = [
    "BTW_PATTERN",
    "SIDE_QUESTION_WRAPPER",
    "extract_side_question_response",
    "find_btw_trigger_positions",
    "run_side_question",
    "wrap_side_question",
]
