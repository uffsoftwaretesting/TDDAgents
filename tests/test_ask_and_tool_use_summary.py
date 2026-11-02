"""`ask()`, the engine's model/read-cache surface, the transcript seed and tool-use summaries (Part 8e)."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.query.config import Gates, RunConfig
from app.query.query import _text_blocks, start_tool_use_summary
from app.query.state import LoopState
from app.query.transitions import Terminated
from app.query_engine import QueryEngine, ResultMessage, ask
from app.services.tool_use_summary.tool_use_summary_generator import (
    E_TOOL_USE_SUMMARY_GENERATION_FAILED,
    ToolInfo,
    generate_tool_use_summary,
    tool_use_summary_system_prompt,
    truncate_json,
)
from app.skills.load_skills_dir import SkillRegistry
from app.state.app_state_store import AppStateStore
from app.tool import tool_context_for
from app.types.permissions import ToolPermissionContext
from app.utils.abort_controller import AbortController
from app.utils.file import strip_line_number_prefix
from app.utils.file_state_cache import FileState
from app.utils.message_queue_manager import CommandQueue, QueuedCommand
from app.utils.messages import (
    Message,
    ToolCall,
    ToolUseSummaryMessage,
    create_tool_use_summary_message,
)
from app.utils.query_helpers import extract_read_files_from_messages
from tests.hook_doubles import ScriptedDispatcher
from tests.test_loop_engine import (
    CONFIG,
    FakeModel,
    asks_for,
    collect,
    make_fake_deps,
    start,
)
from tests.test_loop_query_engine import FakeTranscript


def _ctx() -> Any:
    return tool_context_for(AppStateStore(), hook_dispatcher=ScriptedDispatcher(), skill_registry=SkillRegistry([]),
                            permission_context=ToolPermissionContext(should_avoid_permission_prompts=True))


# ── ask() ────────────────────────────────────────────────────────────────────

def test_ask_runs_one_prompt_through_a_new_engine_over_the_callers_list():
    ctx = _ctx()
    conversation: list[Any] = [HumanMessage(content="before")]
    cache = {"a.py": FileState(content="old")}
    handed_back: list[dict[str, Any]] = []
    engines: list[QueryEngine] = []
    model = FakeModel([[AIMessage(content="done")]])

    async def go() -> list[Any]:
        return [e async for e in ask(
            "next", config=CONFIG, deps=make_fake_deps(model), transcript=FakeTranscript(), session_id="s9",
            context=ctx, mutable_messages=conversation, get_read_file_cache=lambda: cache,
            set_read_file_cache=handed_back.append, on_engine=engines.append,
        )]

    events = asyncio.run(go())
    assert isinstance(events[-1], ResultMessage) and events[-1].session_id == "s9"
    assert [m.content for m in conversation][:2] == ["before", "next"] and conversation[-1].content == "done"
    [engine] = engines
    assert engine.get_messages() == conversation and engine.get_session_id() == "s9"
    [returned] = handed_back
    assert returned == cache and returned is not cache  # a clone went in, the engine's copy came back
    assert returned is engine.get_read_file_state()
    assert model.seen[0].tool_context.read_file_state is returned


def test_ask_hands_the_cache_back_even_when_the_turn_fails():
    ctx = _ctx()
    handed_back: list[Any] = []

    async def broken(state: LoopState, config: RunConfig) -> AsyncIterator[Message]:
        raise RuntimeError("provider down")
        yield  # pragma: no cover

    async def go() -> None:
        async for _ in ask("x", config=CONFIG, deps=make_fake_deps(broken), transcript=FakeTranscript(),
                           session_id="s", context=ctx, mutable_messages=[], get_read_file_cache=dict,
                           set_read_file_cache=handed_back.append):
            pass

    asyncio.run(go())
    assert handed_back == [{}]


def test_set_model_chooses_the_model_of_the_next_turns():
    ctx = _ctx()
    model = FakeModel([[AIMessage(content="a")], [AIMessage(content="b")]])
    engine = QueryEngine(CONFIG, make_fake_deps(model), transcript=FakeTranscript(), session_id="s", context=ctx,
                         user_specified_model="haiku")

    async def turn(prompt: str) -> None:
        async for _ in engine.submit_message(prompt):
            pass

    asyncio.run(turn("one"))
    assert "haiku" in str(model.seen[0].tool_context.main_loop_model)
    engine.set_model("claude-sonnet-5-5")
    asyncio.run(turn("two"))
    assert model.seen[1].tool_context.main_loop_model == "claude-sonnet-5-5"
    plain = QueryEngine(CONFIG, make_fake_deps(model), transcript=FakeTranscript(), session_id="s")
    assert plain.user_specified_model is None


def test_a_meta_prompt_is_hidden_from_the_user_but_sent():
    model = FakeModel([[AIMessage(content="ok")]])
    engine = QueryEngine(CONFIG, make_fake_deps(model), transcript=FakeTranscript(), session_id="s", context=_ctx())

    async def go() -> None:
        async for _ in engine.submit_message("<task-notification>x</task-notification>", is_meta=True):
            pass

    asyncio.run(go())
    sent = next(m for m in model.seen[0].messages if m.content == "<task-notification>x</task-notification>")
    assert sent.additional_kwargs == {"is_meta": True}


def test_a_queued_notification_goes_through_process_user_input(monkeypatch):
    from types import SimpleNamespace

    from app.cli.print import wait_for_background_work

    d = ScriptedDispatcher()
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=d, skill_registry=SkillRegistry([]),
                           permission_context=ToolPermissionContext(should_avoid_permission_prompts=True),
                           command_queue=CommandQueue())
    ctx.command_queue.enqueue_pending_notification(
        QueuedCommand(value="<task-notification>done</task-notification>", mode="task-notification", is_meta=True))
    model = FakeModel([[AIMessage(content="noted")]])
    engine = QueryEngine(CONFIG, make_fake_deps(model), transcript=FakeTranscript(), session_id="s", context=ctx)
    shown: list[Any] = []
    run = SimpleNamespace(ctx=ctx, store=AppStateStore(), engine=engine, config=CONFIG, deps=engine.deps,
                          transcript=engine.transcript, run_id="s", messages=[HumanMessage(content="start")],
                          user_specified_model=None, on_message=shown.append, max_turns=None,
                          max_budget_usd=None, json_schema=None)
    asyncio.run(wait_for_background_work(run))  # type: ignore[arg-type]
    assert d.seen[0][1]["prompt"] == "<task-notification>done</task-notification>"
    sent = model.seen[0].messages
    assert sent[0].content == "start" and sent[1].additional_kwargs == {"is_meta": True}
    assert run.engine is not engine and isinstance(shown[-1], ResultMessage)


# ── extractReadFilesFromMessages ─────────────────────────────────────────────

def test_strip_line_number_prefix():
    assert strip_line_number_prefix("   12\tcode") == "code"
    assert strip_line_number_prefix("3→x") == "x"
    assert strip_line_number_prefix("no number") == "no number"
    assert strip_line_number_prefix("12 code") == "12 code"


def test_the_read_cache_is_rebuilt_from_the_transcript(make_fake_workspace):
    ws = make_fake_workspace()
    ws.write_file("edited.py", "after = 2\n")
    ctx = tool_context_for(AppStateStore(), workspace=ws)

    def call(name: str, call_id: str, **args: Any) -> AIMessage:
        return AIMessage(content="", tool_calls=[{"name": name, "id": call_id, "args": args}])

    messages = [
        call("ReadFile", "r1", file_path="a.py"),
        ToolMessage(content="1\tx = 1\n2\ty = 2<system-reminder>\nnote\n</system-reminder>\n", tool_call_id="r1"),
        call("ReadFile", "r2", file_path="ranged.py", offset=3),
        ToolMessage(content="3\tz", tool_call_id="r2"),
        call("WriteFile", "w1", file_path="new.py", content="print(1)\n"),
        ToolMessage(content="ok", tool_call_id="w1"),
        call("WriteFile", "w2", file_path="empty.py", content=""),
        ToolMessage(content="ok", tool_call_id="w2"),
        call("Edit", "e1", file_path="edited.py", old_string="1", new_string="2"),
        ToolMessage(content="ok", tool_call_id="e1"),
        call("Edit", "e2", file_path="gone.py", old_string="1", new_string="2"),
        ToolMessage(content="ok", tool_call_id="e2"),
        call("Edit", "e3", file_path="failed.py", old_string="1", new_string="2"),
        ToolMessage(content="no", tool_call_id="e3", status="error"),
        call("Glob", "g1", pattern="*"),
        ToolMessage(content="a.py", tool_call_id="g1"),
        call("ReadFile", "r3"),
        HumanMessage(content="unrelated"),
    ]
    cache = extract_read_files_from_messages(messages, ctx)
    assert cache == {"a.py": FileState(content="x = 1\ny = 2"), "new.py": FileState(content="print(1)\n"),
                     "edited.py": FileState(content="after = 2\n")}


def test_a_resumed_session_starts_from_the_seeded_cache(make_fake_workspace):
    from types import SimpleNamespace

    from app.cli.print import seed_read_file_state

    ctx = tool_context_for(AppStateStore(), workspace=make_fake_workspace())
    ctx.read_file_state["kept.py"] = FileState(content="live")
    run = SimpleNamespace(ctx=ctx)

    def write(call_id: str, path: str, content: str) -> AIMessage:
        args = {"file_path": path, "content": content}
        return AIMessage(content="", tool_calls=[{"name": "WriteFile", "id": call_id, "args": args}])

    history = [write("w", "kept.py", "old"), ToolMessage(content="ok", tool_call_id="w"),
               write("v", "b.py", "b"), ToolMessage(content="ok", tool_call_id="v")]
    seed_read_file_state(run, history)  # type: ignore[arg-type]
    assert ctx.read_file_state == {"kept.py": FileState(content="live"), "b.py": FileState(content="b")}


# ── generateToolUseSummary ───────────────────────────────────────────────────

def test_the_system_prompt_is_upstreams_verbatim():
    source = Path("reference/claude-code/src/services/toolUseSummary/toolUseSummaryGenerator.ts")
    if not source.exists():
        pytest.skip("reference snapshot absent")
    text = source.read_text()
    start = text.index("TOOL_USE_SUMMARY_SYSTEM_PROMPT = `") + len("TOOL_USE_SUMMARY_SYSTEM_PROMPT = `")
    assert tool_use_summary_system_prompt() == text[start:text.index("`", start)]


def test_truncate_json():
    assert truncate_json({"a": 1}, 300) == '{"a":1}'
    assert truncate_json("x" * 10, 8) == '"xxxx...'
    assert truncate_json("x" * 6, 8) == '"xxxxxx"'
    assert truncate_json({1, 2}, 300) == "[unable to serialize]"
    assert truncate_json("é", 300) == '"é"'


def test_the_summary_request_and_its_answer():
    asked: list[tuple[str, str]] = []

    async def query(system: str, user: str) -> str:
        asked.append((system, user))
        return "  Read config.json \n"

    tools = [ToolInfo(name="ReadFile", input={"file_path": "c.json"}, output="{}"),
             ToolInfo(name="Bash", input={"command": "ls"}, output=None)]
    summary = asyncio.run(generate_tool_use_summary(tools, query, last_assistant_text="L" * 250))
    assert summary == "Read config.json"
    system, user = asked[0]
    assert system == tool_use_summary_system_prompt()
    assert user == (f"User's intent (from assistant's last message): {'L' * 200}\n\nTools completed:\n\n"
                    'Tool: ReadFile\nInput: {"file_path":"c.json"}\nOutput: "{}"\n\n'
                    'Tool: Bash\nInput: {"command":"ls"}\nOutput: null\n\nLabel:')
    asyncio.run(generate_tool_use_summary(tools[:1], query))
    assert asked[1][1].startswith("Tools completed:\n\n")


def test_no_summary_without_tools_an_answer_or_a_working_model(caplog):
    async def empty(system: str, user: str) -> str:
        return "   "

    async def failing(system: str, user: str) -> str:
        raise RuntimeError("rate limited")

    tools = [ToolInfo(name="X", input={}, output="")]
    assert asyncio.run(generate_tool_use_summary([], empty)) is None
    assert asyncio.run(generate_tool_use_summary(tools, empty)) is None
    assert asyncio.run(generate_tool_use_summary(tools, failing)) is None
    assert f"errorId {E_TOOL_USE_SUMMARY_GENERATION_FAILED}" in caplog.text and "rate limited" in caplog.text


def test_the_summary_message():
    message = create_tool_use_summary_message("Ran tests", ["c1", "c2"])
    expected = ("tool_use_summary", "Ran tests", ("c1", "c2"))
    assert (message.type, message.summary, message.preceding_tool_use_ids) == expected
    assert len(message.uuid) == 36 and message.timestamp.endswith("+00:00")


# ── the loop's pendingToolUseSummary ─────────────────────────────────────────

def _summary_config(on: bool) -> RunConfig:
    return dataclasses.replace(CONFIG, gates=Gates(coordinator_mode=False, emit_tool_use_summaries=on))


def _labelling_deps(model: Any, label: str = "Ran tests", asked: list[Any] | None = None) -> Any:
    async def query(system: str, user: str) -> str:
        if asked is not None:
            asked.append(user)
        return label

    return dataclasses.replace(make_fake_deps(model), query_haiku=lambda context: query)


def _run(config: RunConfig, deps: Any, state: LoopState | None = None) -> list[Any]:
    from app.query.query import run_loop

    async def go() -> list[Any]:
        return [e async for e in run_loop(state or start(), config, deps)]

    return asyncio.run(go())


def test_a_batch_is_labelled_during_the_next_model_call():
    asked: list[str] = []
    # One message per content block, as upstream: the last assistant message is the tool call,
    # which has no text, so the request carries no intent.
    model = FakeModel([[AIMessage(content="I will run the tests"), asks_for("RunTests", "c1")],
                       [AIMessage(content="done")]])
    events = _run(_summary_config(True), _labelling_deps(model, asked=asked))
    summaries = [e for e in events if isinstance(e, ToolUseSummaryMessage)]
    assert [(s.summary, s.preceding_tool_use_ids) for s in summaries] == [("Ran tests", ("c1",))]
    assert asked[0] == 'Tools completed:\n\nTool: RunTests\nInput: {}\nOutput: "ran RunTests"\n\nLabel:'
    with_text = AIMessage(content=[{"type": "text", "text": "Running them"}],
                          tool_calls=[{"name": "RunTests", "args": {}, "id": "c2"}])
    _run(_summary_config(True), _labelling_deps(FakeModel([[with_text], [AIMessage(content="ok")]]), asked=asked))
    assert asked[1].startswith("User's intent (from assistant's last message): Running them\n\nTools completed:")
    done = next(i for i, e in enumerate(events) if isinstance(e, AIMessage) and e.content == "done")
    assert events.index(summaries[0]) > done  # after the next call's stream
    assert isinstance(events[-1], Terminated)


def test_no_label_when_off_for_a_worker_or_after_an_abort():
    model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
    assert not any(isinstance(e, ToolUseSummaryMessage) for e in _run(_summary_config(False), _labelling_deps(model)))
    ctx = start().tool_context
    turn = AbortController()
    calls: list[ToolCall] = [{"name": "X", "args": {}, "id": "c"}]
    config = _summary_config(True)
    deps = _labelling_deps(FakeModel([]))
    assert start_tool_use_summary(config, ctx, [], [], [], turn, deps) is None
    worker = dataclasses.replace(ctx, agent_id="w1")
    assert start_tool_use_summary(config, worker, calls, [], [], turn, deps) is None
    turn.abort()
    assert start_tool_use_summary(config, ctx, calls, [], [], turn, deps) is None
    cancelled = start().tool_context
    cancelled.cancel.cancel()
    assert start_tool_use_summary(config, cancelled, calls, [], [], AbortController(), deps) is None


def test_an_empty_or_failing_label_is_no_message():
    calls: list[ToolCall] = [{"name": "X", "args": {"a": 1}, "id": "c"}]
    ctx = start().tool_context

    async def go(deps: Any) -> Any:
        pending = start_tool_use_summary(_summary_config(True), ctx, calls, [AIMessage(content="")],
                                         [ToolMessage(content="out", tool_call_id="c")], AbortController(), deps)
        assert pending is not None
        return await pending

    assert asyncio.run(go(_labelling_deps(FakeModel([]), label=""))) is None

    def broken_factory(context: Any) -> Any:
        raise RuntimeError("no model")

    assert asyncio.run(go(dataclasses.replace(make_fake_deps(FakeModel([])), query_haiku=broken_factory))) is None
    labelled = asyncio.run(go(_labelling_deps(FakeModel([]), label="Did X")))
    assert (labelled.summary, labelled.preceding_tool_use_ids) == ("Did X", ("c",))


def test_text_blocks_of_an_assistant_message():
    assert _text_blocks(AIMessage(content="plain")) == ["plain"]
    assert _text_blocks(AIMessage(content="")) == []
    assert _text_blocks(AIMessage(content=[{"type": "text", "text": "a"}, {"type": "tool_use"}, "x",
                                           {"type": "text", "text": "b"}])) == ["a", "b"]


def test_a_worker_never_forwards_a_summary(monkeypatch):
    # `run_agent` skips the type the loop could yield; a worker's loop never starts one.
    events = collect(FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]]))
    assert not any(isinstance(e, ToolUseSummaryMessage) for e in events)
