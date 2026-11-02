"""`--json-schema`: `StructuredOutput` (`SyntheticOutputTool.ts`), its Stop enforcement and retry limit."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.cli.print import HeadlessOptions, run_headless
from app.tools.synthetic_output_tool.synthetic_output_tool import (
    STRUCTURED_OUTPUT_PROVIDED,
    SYNTHETIC_OUTPUT_TOOL_NAME,
    create_synthetic_output_tool,
    is_synthetic_output_tool_enabled,
    render_tool_use_message,
)
from app.utils.attachments import attachment_of
from app.utils.messages import count_tool_calls, has_successful_tool_call, is_signal_attachment_message
from tests.test_stream_json import Model, lines_of, run_options

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
          "required": ["name"]}


def so_call(call_id: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": SYNTHETIC_OUTPUT_TOOL_NAME, "id": call_id, "args": args}])


# ── the tool ─────────────────────────────────────────────────────────────────

def test_an_invalid_schema_is_its_own_error() -> None:
    error = create_synthetic_output_tool({"type": "nonsense"})
    assert isinstance(error, str) and "nonsense" in error


def test_the_tool_validates_its_input_against_the_schema() -> None:
    tool = create_synthetic_output_tool(SCHEMA)
    assert not isinstance(tool, str)
    assert (tool.name, tool.input_schema) == (SYNTHETIC_OUTPUT_TOOL_NAME, SCHEMA)
    assert tool.prompt.startswith("Use this tool to return your final response in the requested structured format.")
    assert tool.description({}) == "Return structured output in the requested format"
    assert tool.is_read_only({}) and tool.is_concurrency_safe({})
    ok = asyncio.run(_call(tool, {"name": "Ana", "age": 3}))
    assert (ok.content, ok.is_error) == (STRUCTURED_OUTPUT_PROVIDED, False)
    (signal,) = ok.new_messages
    assert attachment_of(signal) == {"type": "structured_output", "data": {"name": "Ana", "age": 3}}
    assert is_signal_attachment_message(signal)
    bad = asyncio.run(_call(tool, {"age": "three"}))
    assert bad.is_error and bad.new_messages == ()
    assert bad.content == ("Output does not match required schema: root: 'name' is a required property, "
                           "/age: 'three' is not of type 'integer'")


async def _call(tool: Any, args: dict[str, Any]) -> Any:
    result = tool.call(args, None)
    return await result if asyncio.iscoroutine(result) else result


def test_render_and_enablement() -> None:
    assert render_tool_use_message({}) is None
    assert render_tool_use_message({"a": 1, "b": "x"}) == 'a: 1, b: "x"'
    assert render_tool_use_message({"a": 1, "b": 2, "c": 3, "d": 4}) == "4 fields: a, b, c…"
    assert is_synthetic_output_tool_enabled(is_non_interactive_session=True)
    assert not is_synthetic_output_tool_enabled(is_non_interactive_session=False)


# ── the message helpers ──────────────────────────────────────────────────────

def test_count_and_successful_tool_calls() -> None:
    first = so_call("a", {})
    other = AIMessage(content="", tool_calls=[{"name": "Bash", "id": "b", "args": {}}])
    failed = ToolMessage(content="bad", tool_call_id="a", status="error")
    messages: list[Any] = [HumanMessage(content="go"), first, failed, other]
    assert count_tool_calls(messages, SYNTHETIC_OUTPUT_TOOL_NAME) == 1
    assert count_tool_calls([first, so_call("c", {}), so_call("d", {})], SYNTHETIC_OUTPUT_TOOL_NAME, 2) == 2
    assert not has_successful_tool_call(messages, SYNTHETIC_OUTPUT_TOOL_NAME)
    assert not has_successful_tool_call([HumanMessage(content="x")], SYNTHETIC_OUTPUT_TOOL_NAME)
    assert not has_successful_tool_call([first], SYNTHETIC_OUTPUT_TOOL_NAME)
    retried = [*messages, so_call("c", {}), ToolMessage(content="ok", tool_call_id="c")]
    assert has_successful_tool_call(retried, SYNTHETIC_OUTPUT_TOOL_NAME)
    assert not is_signal_attachment_message(HumanMessage(content="text"))


def test_signals_never_reach_the_request() -> None:
    from app.query.config import build_run_config
    from app.query.state import initial_loop_state
    from app.services.api.claude import build_request_messages
    from app.state.app_state_store import AppStateStore
    from app.tool import tool_context_for
    from app.utils.attachments import Attachment, create_signal_attachment_message

    signal = create_signal_attachment_message(Attachment("structured_output", {"data": {}}))
    state = initial_loop_state((HumanMessage(content="task"), signal), tool_context_for(AppStateStore()))
    import app.services.api.claude as claude

    original = claude.request_system_texts
    claude.request_system_texts = lambda s, c: ["system"]  # type: ignore[assignment]
    try:
        request = build_request_messages(state, build_run_config("r"))
    finally:
        claude.request_system_texts = original
    assert signal not in request and any(m.content == "task" for m in request)


# ── end to end ───────────────────────────────────────────────────────────────

def json_run(python_workspace: Any, tmp_path: Path, model: Model, session: str, **options: Any) -> dict[str, Any]:
    written: list[str] = []
    opts = HeadlessOptions(output_format="json", json_schema=SCHEMA, **options)
    asyncio.run(run_headless(session, "Who?", workspace=python_workspace, options=opts,
                             write_stdout=written.append, **run_options(tmp_path, model)))
    (result,) = lines_of(written)
    return result


def test_the_result_carries_the_structured_output(python_workspace: Any, tmp_path: Path) -> None:
    model = Model([[so_call("s1", {"name": "Ana"})], [AIMessage(content="Here it is.")]])
    result = json_run(python_workspace, tmp_path, model, "so-ok")
    assert (result["subtype"], result["structured_output"], result["result"]) == (
        "success", {"name": "Ana"}, "Here it is.")
    tools = [t.name for t in model.seen[0].tool_context.tools]
    assert tools[-1] == SYNTHETIC_OUTPUT_TOOL_NAME


def test_stopping_without_it_is_refused_until_it_is_called(python_workspace: Any, tmp_path: Path) -> None:
    model = Model([[AIMessage(content="Ana.")], [so_call("s1", {"name": "Ana"})], [AIMessage(content="Done.")]])
    result = json_run(python_workspace, tmp_path, model, "so-enforced")
    assert result["structured_output"] == {"name": "Ana"}
    reminders = [m for m in model.seen[1].messages if isinstance(m, HumanMessage)
                 and "You MUST call the StructuredOutput tool to complete this request." in str(m.content)]
    assert reminders


def test_the_retry_limit(python_workspace: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_STRUCTURED_OUTPUT_RETRIES", "2")
    model = Model([[so_call("s1", {"age": 1})], [so_call("s2", {"age": 2})], [AIMessage(content="never")]])
    result = json_run(python_workspace, tmp_path, model, "so-retries")
    assert (result["subtype"], result["is_error"], result["errors"]) == (
        "error_max_structured_output_retries", True, ["Failed to provide valid structured output after 2 attempts"])


def test_an_invalid_schema_adds_no_tool(python_workspace: Any, tmp_path: Path) -> None:
    written: list[str] = []
    model = Model([[AIMessage(content="plain")]])
    opts = dataclasses.replace(HeadlessOptions(output_format="json"), json_schema={"type": "nonsense"})
    asyncio.run(run_headless("so-bad", "Who?", workspace=python_workspace, options=opts,
                             write_stdout=written.append, **run_options(tmp_path, model)))
    assert SYNTHETIC_OUTPUT_TOOL_NAME not in [t.name for t in model.seen[0].tool_context.tools]
    assert json.loads(written[0])["result"] == "plain"


def test_the_json_schema_flag() -> None:
    import argparse

    import app.main as entry

    assert entry.headless_options(entry.parse_args(["-p", "x", "--json-schema", json.dumps(SCHEMA)])).json_schema == (
        SCHEMA)
    for bad, message in (("{", "--json-schema is not valid JSON"), ("[1]", "--json-schema must be a JSON object")):
        with pytest.raises(argparse.ArgumentTypeError, match=message):
            entry.json_schema_flag(bad)
