"""Phase 9: the runner's per-item run id, upstream resume of an unfinished item, the
startup flags, and the per-item cache reset."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from typing import Any
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop import runner as runner_mod
from app.loop.factory import get_production_deps
from app.loop.runner import item_run_id, tdd_loop_runner
from app.loop.storage.paths import get_project_dir
from app.loop.storage.recovery import CONTINUATION_MESSAGE
from app.loop.storage.session_storage import TranscriptStore


def _run(make_fake_workspace, model, tmp_path, session_id: Any = "s", idx: int = 2, **kwargs: Any) -> Any:
    deps = dataclasses.replace(get_production_deps(), call_model=model)
    return asyncio.run(tdd_loop_runner(
        "the spec", idx, {"session_id": session_id}, workspace=make_fake_workspace(), deps=deps,
        project_root=tmp_path, home=tmp_path / "home", managed_dir=tmp_path / "etc", **kwargs))


def _texts(messages: Any) -> list[str]:
    return [m.content for m in messages if isinstance(m.content, str)]


def _recording_model(seen: list[Any]) -> Any:
    async def model(state, config):
        seen.append((state, config))
        yield AIMessage(content="done")

    return model


def test_item_run_id_is_stable_per_session_and_index() -> None:
    a = item_run_id("tdd-1", 0)
    assert a == item_run_id("tdd-1", 0)
    assert re.fullmatch(r"loop-[0-9a-f]{32}", a)
    assert a != item_run_id("tdd-1", 1)
    assert a != item_run_id("tdd-2", 0)


def test_item_run_id_without_a_session_is_fresh() -> None:
    for missing in (None, ""):
        first, second = item_run_id(missing, 0), item_run_id(missing, 0)
        assert first != second
        assert re.fullmatch(r"loop-[0-9a-f]{32}", first)


def test_the_transcript_is_named_by_the_item_run_id(make_fake_workspace, tmp_path) -> None:
    _run(make_fake_workspace, _recording_model([]), tmp_path)
    assert (get_project_dir(str(tmp_path)) / f"{item_run_id('s', 2)}.jsonl").is_file()


def test_startup_flags_reach_the_run_config(make_fake_workspace, tmp_path) -> None:
    seen: list[Any] = []
    _run(make_fake_workspace, _recording_model(seen), tmp_path, postgres_checkpointing=True,
         permission_mode="acceptEdits", settings_path=str(tmp_path / "flag.json"), dangerously_skip_permissions=True)
    config = seen[0][1]
    assert config.run_id == item_run_id("s", 2)
    assert config.gates.postgres_checkpointing is True
    assert config.permission_mode == "acceptEdits"
    assert config.settings_path == str(tmp_path / "flag.json")
    assert config.dangerously_skip_permissions is True


def test_flags_default_to_off(make_fake_workspace, tmp_path) -> None:
    seen: list[Any] = []
    _run(make_fake_workspace, _recording_model(seen), tmp_path)
    config = seen[0][1]
    assert config.gates.postgres_checkpointing is False
    assert config.permission_mode is None
    assert config.settings_path is None
    assert config.dangerously_skip_permissions is False


def test_a_fresh_item_starts_with_session_start_startup(make_fake_workspace, tmp_path) -> None:
    sources: list[str] = []
    from app.loop.hooks import session as hook_session

    original = hook_session.process_session_start_hooks

    async def recording(dispatcher, source, agent_type=None, model=None):
        sources.append(source)
        return await original(dispatcher, source, agent_type, model)

    seen: list[Any] = []
    with patch.object(hook_session, "process_session_start_hooks", recording):
        _run(make_fake_workspace, _recording_model(seen), tmp_path)
    assert sources == ["startup"]
    assert _texts(seen[0][0].messages)[0] == "the spec"


def test_a_rerun_item_resumes_its_conversation(make_fake_workspace, tmp_path) -> None:
    from app.loop.hooks import session as hook_session

    original = hook_session.process_session_start_hooks
    sources: list[str] = []

    async def recording(dispatcher, source, agent_type=None, model=None):
        sources.append(source)
        return await original(dispatcher, source, agent_type, model)

    first: list[Any] = []
    _run(make_fake_workspace, _recording_model(first), tmp_path)
    second: list[Any] = []
    with patch.object(hook_session, "process_session_start_hooks", recording):
        result = _run(make_fake_workspace, _recording_model(second), tmp_path)

    assert sources == ["resume"]
    texts = _texts(second[0][0].messages)
    # The first run's conversation, then the sub-requirement submitted again as the new prompt.
    assert texts.count("the spec") == 2
    assert "done" in texts
    assert texts.index("done") < len(texts) - 1
    # The ledger is never restored: the resumed run still has no observed cycle.
    assert result.red_confirmed is False and result.green_passed is False

    # One transcript, one chain: the second run continued the first's tip.
    path = get_project_dir(str(tmp_path)) / f"{item_run_id('s', 2)}.jsonl"
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    uuids = [e["uuid"] for e in entries]
    assert len(uuids) == len(set(uuids))
    assert sum(1 for e in entries if e["parentUuid"] is None) == 1


def _seed_transcript(tmp_path, messages: list[Any]) -> None:
    store = TranscriptStore(item_run_id("s", 2), str(tmp_path))
    store.record_transcript(messages)


def test_an_interrupted_prompt_is_submitted_again_once(make_fake_workspace, tmp_path) -> None:
    _seed_transcript(tmp_path, [HumanMessage(content="the spec"), AIMessage(content="working"),
                                HumanMessage(content="also handle negatives")])
    seen: list[Any] = []
    _run(make_fake_workspace, _recording_model(seen), tmp_path)
    texts = _texts(seen[0][0].messages)
    assert texts.count("also handle negatives") == 1
    assert texts.count("the spec") == 1
    assert "No response requested." not in texts
    assert texts.index("working") < texts.index("also handle negatives")


def test_a_mid_turn_interruption_continues_from_where_it_left_off(make_fake_workspace, tmp_path) -> None:
    call = {"id": "t1", "name": "Glob", "args": {"pattern": "*"}}
    _seed_transcript(tmp_path, [HumanMessage(content="the spec"), AIMessage(content="", tool_calls=[call]),
                                ToolMessage(content="no files", tool_call_id="t1")])
    seen: list[Any] = []
    _run(make_fake_workspace, _recording_model(seen), tmp_path)
    texts = _texts(seen[0][0].messages)
    assert texts.count(CONTINUATION_MESSAGE) == 1
    assert texts.count("the spec") == 1
    assert "no files" in texts


def test_an_empty_transcript_starts_fresh(make_fake_workspace, tmp_path) -> None:
    path = get_project_dir(str(tmp_path)) / f"{item_run_id('s', 2)}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    seen: list[Any] = []
    _run(make_fake_workspace, _recording_model(seen), tmp_path)
    assert _texts(seen[0][0].messages)[0] == "the spec"


def test_prompt_text_of_block_content() -> None:
    blocks: list[Any] = [{"type": "text", "text": "a"}, {"type": "image"}, "x", {"type": "text", "text": "b"}]
    message = HumanMessage(content=blocks)
    assert runner_mod._prompt_text(message) == "ab"
    assert runner_mod._prompt_text(HumanMessage(content="plain")) == "plain"


def test_the_session_context_is_reset_after_each_item(make_fake_workspace, tmp_path) -> None:
    reset: list[Any] = []
    with patch("app.loop.context.assembly.reset_session_context", reset.append):
        _run(make_fake_workspace, _recording_model([]), tmp_path)
    assert reset == [item_run_id("s", 2)]
