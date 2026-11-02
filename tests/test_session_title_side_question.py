"""`generate_session_title` (`sessionTitle.ts`) and `side_question` (`sideQuestion.ts`)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.cli.print import HeadlessOptions, run_headless
from app.utils.session_storage import TranscriptStore
from app.utils.session_title import (
    MAX_CONVERSATION_TEXT,
    SESSION_TITLE_PROMPT,
    extract_conversation_text,
    generate_session_title,
    parse_title,
)
from app.utils.side_question import (
    SIDE_QUESTION_WRAPPER,
    extract_side_question_response,
    find_btw_trigger_positions,
    run_side_question,
    wrap_side_question,
)
from tests.test_stream_json import Feed, Model, control, lines_of, responses, run_options

STREAM = HeadlessOptions(output_format="stream-json", input_format="stream-json", verbose=True)


def test_the_conversation_text_a_title_is_made_from() -> None:
    messages = [HumanMessage(content="build a calc"), HumanMessage(content="meta", additional_kwargs={"is_meta": True}),
                AIMessage(content=[{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "x"}]),
                object()]
    assert extract_conversation_text(messages) == "build a calc\nok"
    long = [HumanMessage(content="a" * (MAX_CONVERSATION_TEXT + 5) + "END")]
    text = extract_conversation_text(long)
    assert len(text) == MAX_CONVERSATION_TEXT and text.endswith("END")


def test_parsing_and_generating_a_title() -> None:
    assert parse_title('Sure: {"title": " Add OAuth "}') == "Add OAuth"
    for bad in ('{"title": "  "}', "no json", '{"name": "x"}', '["title"]', '{"title": 3}'):
        assert parse_title(bad) is None
    asked: list[tuple[str, str]] = []

    async def haiku(system: str, user: str) -> str:
        asked.append((system, user))
        return '{"title": "Fix login button"}'

    async def broken(system: str, user: str) -> str:
        raise RuntimeError("down")

    assert asyncio.run(generate_session_title("  fix the login  ", haiku)) == "Fix login button"
    assert asked == [(SESSION_TITLE_PROMPT, "fix the login")]
    assert asyncio.run(generate_session_title("   ", haiku)) is None
    assert asyncio.run(generate_session_title("x", broken)) is None


def test_a_title_is_saved_in_the_transcript(tmp_path: Path) -> None:
    store = TranscriptStore("loop-1", str(tmp_path))
    store.record_transcript([HumanMessage(content="hi")])
    store.save_ai_generated_title("Fix login button")
    entries = [json.loads(line) for line in store.path.read_text().splitlines()]
    assert {"type": "ai-title", "aiTitle": "Fix login button", "sessionId": "loop-1"} in entries


def test_side_question_helpers() -> None:
    assert find_btw_trigger_positions("/btw what is x") == [{"word": "/btw", "start": 0, "end": 4}]
    assert find_btw_trigger_positions("ask /btw later") == []
    assert wrap_side_question("why?") == SIDE_QUESTION_WRAPPER + "why?"
    assert SIDE_QUESTION_WRAPPER.startswith("<system-reminder>This is a side question from the user.")
    assert extract_side_question_response([AIMessage(content="Because."), AIMessage(content=" More ")]) == (
        "Because.\n\n More")
    tool = AIMessage(content="", tool_calls=[{"name": "Bash", "id": "t", "args": {}}])
    assert extract_side_question_response([tool]) == (
        "(The model tried to call Bash instead of answering directly. Try rephrasing or ask in the main "
        "conversation.)")
    error = AIMessage(content="overloaded", additional_kwargs={"is_api_error_message": True})
    assert extract_side_question_response([error]) == "(API error: overloaded)"
    assert extract_side_question_response([HumanMessage(content="x")]) is None


def test_a_side_question_sees_the_conversation_but_leaves_it() -> None:
    seen: list[Any] = []

    async def call_model(state: Any, config: Any) -> Any:
        seen.append(state.messages)
        yield AIMessage(content="It is 4.")

    @dataclasses.dataclass
    class State:
        messages: tuple[Any, ...]

    state = State(messages=(HumanMessage(content="2+2 in the code"),))
    assert asyncio.run(run_side_question("what is it?", state, None, call_model)) == "It is 4."
    assert seen[0][0].content == "2+2 in the code" and seen[0][1].content == wrap_side_question("what is it?")
    assert len(state.messages) == 1


def test_both_control_requests_end_to_end(python_workspace: Any, tmp_path: Path) -> None:
    async def haiku(system: str, user: str) -> str:
        return '{"title": "Build a calculator"}'

    options = run_options(tmp_path, Model([[AIMessage(content="A side answer.")]]))
    options["deps"] = dataclasses.replace(options["deps"], query_haiku=lambda context: haiku)
    feed = Feed(control("t", "generate_session_title", description="build a calc", persist=True),
                control("s", "side_question", question="what next?"))
    feed.close()
    written: list[str] = []
    asyncio.run(run_headless("sj-title", None, workspace=python_workspace, options=STREAM,
                             input_blocks=feed.blocks(), write_stdout=written.append, **options))
    got = responses(lines_of(written))
    assert got["t"]["response"] == {"title": "Build a calculator"}
    assert got["s"]["response"] == {"response": "A side answer."}
