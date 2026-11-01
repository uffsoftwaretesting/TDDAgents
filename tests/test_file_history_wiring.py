"""
File checkpointing wired through the session (`app/utils/file_history.py`'s callers): the
transcript records (`sessionStorage.ts`), the resume chain (`conversationRecovery.ts`,
`sessionRestore.ts`), the per-prompt snapshot (`QueryEngine.ts`, `selectableUserMessagesFilter`),
the file tools' `fileHistoryTrackEdit`, and `--rewind-files` (`print.ts`).
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolCall, ToolMessage

import app.main as entry
from app.bootstrap.state import SessionBootstrap
from app.cli.print import open_session_run, restore_session, run_rewind_files, submit_prompt
from app.components.message_selector import (
    SYNTHETIC_MESSAGES,
    is_synthetic_message,
    selectable_user_messages_filter,
)
from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.plan import ItemStatus, TddPlan
from app.tool import tool_context_for
from app.tools.file_write_tool.file_write_tool import build_write_file_tool
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.conversation_recovery import load_conversation_for_resume
from app.utils.file_history import (
    ENABLE_SDK_FILE_CHECKPOINTING_ENV,
    FileHistoryBackup,
    FileHistorySnapshot,
    handle_rewind_files,
    update_file_history_state_for,
)
from app.utils.session_restore import restore_session_state_from_log
from app.utils.session_storage import (
    FILE_HISTORY_SNAPSHOT_TYPE,
    TranscriptStore,
    build_file_history_snapshot_chain,
    load_transcript_file,
    message_uuid,
)
from app.workspace.local import LocalWorkspace

T0 = datetime(2026, 1, 2, tzinfo=UTC)


def snap(message_id: str, version: int) -> FileHistorySnapshot:
    return FileHistorySnapshot(message_id, {"a.py": FileHistoryBackup(f"h@v{version}", version, T0)}, T0)


# ── transcript records ───────────────────────────────────────────────────────


def test_a_snapshot_is_buffered_until_the_session_file_exists(tmp_path: Path) -> None:
    store = TranscriptStore("s1", str(tmp_path), version="v", git_branch=None)
    store.record_file_history_snapshot("u1", snap("u1", 1), False)
    assert not store.path.exists()
    human = HumanMessage(content="go")
    store.record_transcript([human])
    entries = [json.loads(line) for line in store.path.read_text(encoding="utf-8").splitlines()]
    assert entries[0] == {"type": FILE_HISTORY_SNAPSHOT_TYPE, "messageId": "u1",
                          "snapshot": {"messageId": "u1", "trackedFileBackups": {"a.py": {
                              "backupFileName": "h@v1", "version": 1, "backupTime": "2026-01-02T00:00:00.000Z"}},
                              "timestamp": "2026-01-02T00:00:00.000Z"},
                          "isSnapshotUpdate": False}
    assert entries[1]["uuid"] == message_uuid(human)
    store.record_file_history_snapshot("a1", snap("u1", 2), True)
    last = json.loads(store.path.read_text(encoding="utf-8").splitlines()[-1])
    assert (last["messageId"], last["isSnapshotUpdate"]) == ("a1", True)
    # A snapshot entry is not a message: a reopened store does not count it as recorded.
    assert TranscriptStore("s1", str(tmp_path))._recorded == {message_uuid(human)}


def test_a_snapshot_is_not_written_without_persistence(tmp_path: Path) -> None:
    store = TranscriptStore("s1", str(tmp_path), persist=False, version="v", git_branch=None)
    store.record_transcript([HumanMessage(content="go")])
    store.record_file_history_snapshot("u1", snap("u1", 1), False)
    assert not store.path.exists()


def test_the_snapshot_chain_follows_the_conversation_and_applies_updates() -> None:
    conversation = [{"uuid": "u1"}, {"uuid": "a1"}, {"uuid": "u2"}, {"uuid": "x"}]
    entries = {
        "u2": {"snapshot": json_of(snap("u2", 3)), "isSnapshotUpdate": False},
        "u1": {"snapshot": json_of(snap("u1", 1)), "isSnapshotUpdate": False},
        "a1": {"snapshot": json_of(snap("u1", 2)), "isSnapshotUpdate": True},
        "x": {"snapshot": "garbage", "isSnapshotUpdate": False},
        "orphan": {"snapshot": json_of(snap("o", 1)), "isSnapshotUpdate": False},
    }
    chain = build_file_history_snapshot_chain(entries, conversation)
    assert chain == [snap("u1", 2), snap("u2", 3)]
    # An update whose snapshot was never seen starts its own entry.
    assert build_file_history_snapshot_chain({"a1": entries["a1"]}, conversation) == [snap("u1", 2)]


def json_of(snapshot: FileHistorySnapshot) -> Any:
    from app.utils.file_history import snapshot_to_json

    return json.loads(json.dumps(snapshot_to_json(snapshot)))


def test_loading_a_transcript_returns_its_snapshots_and_last_session(tmp_path: Path) -> None:
    store = TranscriptStore("s1", str(tmp_path), version="v", git_branch=None)
    human, reply = HumanMessage(content="go"), AIMessage(content="ok")
    store.record_transcript([human, reply])
    store.record_file_history_snapshot(str(message_uuid(human)), snap(str(message_uuid(human)), 1), False)
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n[1]\n")
    assert load_transcript_file(store.path).file_history_snapshots == [snap(str(message_uuid(human)), 1)]
    resumed = load_conversation_for_resume(store.path)
    assert resumed is not None
    assert resumed.file_history_snapshots == (snap(str(message_uuid(human)), 1),)
    assert resumed.session_id == "s1"
    assert [m.content for m in resumed.messages] == ["go", "ok"]


def test_restore_session_state_sets_the_file_history(tmp_path: Path) -> None:
    bootstrap = SessionBootstrap(session_id="s", original_cwd=str(tmp_path), is_interactive=True)
    store = AppStateStore(AppState(bootstrap=bootstrap))
    restore_session_state_from_log((), store.update, bootstrap)
    assert store.get().file_history.snapshots == ()
    restore_session_state_from_log((snap("u1", 1),), store.update, bootstrap)
    assert store.get().file_history.snapshots == (snap("u1", 1),)
    assert store.get().file_history.tracked_files == frozenset({"a.py"})


# ── which prompts get a snapshot ─────────────────────────────────────────────


@pytest.mark.parametrize(("message", "selectable"), [
    (HumanMessage(content="build it"), True),
    (HumanMessage(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]), True),
    (HumanMessage(content=[{"type": "image", "source": {}}]), True),
    (AIMessage(content="no"), False),
    (ToolMessage(content="r", tool_call_id="c"), False),
    (HumanMessage(content=[{"type": "tool_result", "tool_use_id": "c"}]), False),
    (HumanMessage(content=[{"type": "text", "text": "[Request interrupted by user]"}]), False),
    (HumanMessage(content="m", additional_kwargs={"is_meta": True}), False),
    (HumanMessage(content="s", additional_kwargs={"is_compact_summary": True}), False),
    (HumanMessage(content="v", additional_kwargs={"is_visible_in_transcript_only": True}), False),
    (HumanMessage(content="x", additional_kwargs={"attachment": {"type": "queued_command"}}), False),
    (HumanMessage(content=" <local-command-stdout>out</local-command-stdout>"), False),
    (HumanMessage(content="<local-command-stderr>e"), False),
    (HumanMessage(content="<bash-stdout>"), False),
    (HumanMessage(content="<bash-stderr>"), False),
    (HumanMessage(content="<task-notification>done"), False),
    (HumanMessage(content="<tick>"), False),
    (HumanMessage(content='<teammate-message from="x">'), False),
    (HumanMessage(content=[{"type": "text", "text": "<tick>"}, {"type": "image"}]), True),
])
def test_selectable_user_messages(message: Any, selectable: bool) -> None:
    assert selectable_user_messages_filter(message) is selectable


def test_synthetic_messages_are_upstreams_set() -> None:
    assert len(SYNTHETIC_MESSAGES) == 5
    assert "No response requested." in SYNTHETIC_MESSAGES
    assert is_synthetic_message(HumanMessage(content="No response requested.")) is False
    assert is_synthetic_message(HumanMessage(content=[])) is False
    assert is_synthetic_message(HumanMessage(content=["No response requested."])) is False


# ── the file tools ───────────────────────────────────────────────────────────


def tool_context(tmp_path: Path, *, workspace: Any = None, interactive: bool = True) -> Any:
    ws = workspace if workspace is not None else LocalWorkspace(str(tmp_path / "project"))
    root = str(getattr(ws, "root", tmp_path))
    bootstrap = SessionBootstrap(session_id="s1", original_cwd=root, is_interactive=interactive)
    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=True)
    store = AppStateStore(AppState(tool_permission_context=perm, bootstrap=bootstrap))
    transcript = TranscriptStore("s1", root, version="v", git_branch=None)
    ctx = tool_context_for(store, workspace=ws, run_id="s1", transcript_store=transcript)
    ctx.update_file_history_state = update_file_history_state_for(store.update)
    return ctx, store


def write_call(ctx: Any, path: str, content: str, assistant: AIMessage | None = None) -> Any:
    tool = build_write_file_tool()
    ctx.tools = (tool,)
    call = ToolCall(id="c1", name=tool.name, args={"file_path": path, "content": content})
    message = assistant or AIMessage(content="", tool_calls=[call])
    return asyncio.run(run_tool_use(call, message, ctx, [tool])).message


def test_write_tracks_the_file_before_writing_it(tmp_path: Path) -> None:
    ctx, store = tool_context(tmp_path)
    asyncio.run(_snapshot(store, "u1"))
    assistant = AIMessage(content="", tool_calls=[])
    out = write_call(ctx, "src/a.py", "v1\n", assistant)
    assert out.status == "success", out.content
    state = store.get().file_history
    assert state.tracked_files == frozenset({"src/a.py"})
    # A new file: its pre-edit backup records that it did not exist.
    assert state.snapshots[-1].tracked_file_backups["src/a.py"].backup_file_name is None
    # The assistant message got its transcript uuid, which keys the recorded update.
    assert message_uuid(assistant) is not None


def test_edit_backs_up_the_existing_content(tmp_path: Path) -> None:
    from app.tools.file_edit_tool.file_edit_tool import build_edit_tool
    from app.utils.file_state_cache import FileState

    ctx, store = tool_context(tmp_path)
    ctx.workspace.write_file("a.py", "old\n")
    ctx.read_file_state["a.py"] = FileState(content="old\n")
    asyncio.run(_snapshot(store, "u1"))
    tool = build_edit_tool()
    ctx.tools = (tool,)
    call = ToolCall(id="c1", name=tool.name, args={"file_path": "a.py", "old_string": "old", "new_string": "new"})
    out = asyncio.run(run_tool_use(call, AIMessage(content="", tool_calls=[call]), ctx, [tool])).message
    assert out.status == "success", out.content
    backup = store.get().file_history.snapshots[-1].tracked_file_backups["a.py"]
    from app.utils.file_history import get_file_history_dir

    assert (get_file_history_dir("s1") / str(backup.backup_file_name)).read_text(encoding="utf-8") == "old\n"


def test_no_tracking_without_a_host_root_or_an_assistant_message_or_when_disabled(tmp_path: Path,
                                                                                  monkeypatch: Any) -> None:
    from app.tools.utils import file_history_path, track_file_history_edit

    class NoRoot:
        pass

    ctx, store = tool_context(tmp_path, workspace=NoRoot())
    asyncio.run(_snapshot(store, "u1"))
    assert file_history_path(ctx, "a.py") is None
    ctx.parent_message = AIMessage(content="")
    asyncio.run(track_file_history_edit(ctx, "a.py"))
    assert store.get().file_history.tracked_files == frozenset()

    ctx, store = tool_context(tmp_path)
    asyncio.run(_snapshot(store, "u1"))
    assert file_history_path(ctx, ".") == str(ctx.workspace.root)
    asyncio.run(track_file_history_edit(ctx, "a.py"))  # no parent message
    assert store.get().file_history.tracked_files == frozenset()

    monkeypatch.setenv("TDDAGENTS_DISABLE_FILE_CHECKPOINTING", "1")
    ctx.parent_message = AIMessage(content="")
    asyncio.run(track_file_history_edit(ctx, "a.py"))
    assert store.get().file_history.tracked_files == frozenset()


async def _snapshot(store: AppStateStore, message_id: str) -> None:
    from app.utils.file_history import file_history_make_snapshot

    await file_history_make_snapshot(update_file_history_state_for(store.update), message_id,
                                     store.get().bootstrap)


# ── the session: a snapshot per prompt, resume, rewind ───────────────────────


def call(name: str, cid: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])


def scripted(turns: list[list[AIMessage]]) -> Any:
    from app.query.deps import get_production_deps

    async def model(state: Any, config: Any) -> AsyncIterator[Any]:
        for message in turns.pop(0):
            yield message

    return dataclasses.replace(get_production_deps(), call_model=model)


FINISHED = TddPlan.from_texts(["a"])
FINISHED = dataclasses.replace(FINISHED, items=(dataclasses.replace(FINISHED.items[0], status=ItemStatus.DONE),))
PERM = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=True)


def options(tmp_path: Path, turns: list[list[AIMessage]]) -> dict[str, Any]:
    return {"deps": scripted(turns), "permission_context": PERM, "project_root": tmp_path,
            "home": tmp_path / "home", "managed_dir": tmp_path / "etc"}


async def _never_asked(*args: Any) -> Any:
    raise AssertionError("acceptEdits asks nothing here")


def user_ids(messages: Any) -> dict[str, str]:
    return {str(m.content): str(message_uuid(m)) for m in messages
            if isinstance(m, HumanMessage) and selectable_user_messages_filter(m)}


def test_each_prompt_is_checkpointed_and_the_files_rewind_also_after_resume(tmp_path: Path) -> None:
    ws = LocalWorkspace(str(tmp_path / "thread" / "project"))
    target = Path(ws.root) / "a.py"
    turns = [
        [call("WriteFile", "w1", file_path="a.py", content="v1\n")], [AIMessage(content="written")],
        [call("WriteFile", "w2", file_path="a.py", content="v2\n")], [AIMessage(content="rewritten")],
    ]

    async def session() -> dict[str, str]:
        async with open_session_run(run_id="loop-fh", plan=FINISHED, workspace=ws, can_use_tool=_never_asked,
                                    **options(tmp_path, turns)) as run:
            assert run.store.get().bootstrap == SessionBootstrap("loop-fh", str(ws.root), True)
            await submit_prompt(run, "first")
            await submit_prompt(run, "second")
            ids = user_ids(run.messages)
            state = run.store.get().file_history
            assert [s.message_id for s in state.snapshots] == [ids["first"], ids["second"]]
            assert state.tracked_files == frozenset({"a.py"})
            assert target.read_text(encoding="utf-8") == "v2\n"
            dry = await handle_rewind_files(ids["first"], run.store.get(), run.store.update, True)
            assert (dry.can_rewind, dry.files_changed, dry.deletions) == (True, [str(target)], 1)
            return ids

    ids = asyncio.run(session())

    async def resumed() -> None:
        async with open_session_run(run_id="loop-fh", plan=FINISHED, workspace=ws, can_use_tool=_never_asked,
                                    **options(tmp_path, [])) as run:
            assert await restore_session(run)
            state = run.store.get().file_history
            assert [s.message_id for s in state.snapshots] == [ids["first"], ids["second"]]
            assert state.snapshot_sequence == 2
            result = await handle_rewind_files(ids["second"], run.store.get(), run.store.update, False)
            assert result.can_rewind is True
            assert target.read_text(encoding="utf-8") == "v1\n"
            result = await handle_rewind_files(ids["first"], run.store.get(), run.store.update, False)
            assert result.can_rewind is True
            assert not target.exists()

    asyncio.run(resumed())


def test_a_headless_session_checkpoints_only_with_the_sdk_env(tmp_path: Path, monkeypatch: Any) -> None:
    ws = LocalWorkspace(str(tmp_path / "thread" / "project"))
    monkeypatch.delenv(ENABLE_SDK_FILE_CHECKPOINTING_ENV, raising=False)

    async def session(name: str) -> int:
        async with open_session_run(run_id=name, plan=FINISHED, workspace=ws,
                                    **options(tmp_path, [[AIMessage(content="ok")]])) as run:
            await submit_prompt(run, "hello")
            return len(run.store.get().file_history.snapshots)

    assert asyncio.run(session("loop-a")) == 0
    monkeypatch.setenv(ENABLE_SDK_FILE_CHECKPOINTING_ENV, "1")
    assert asyncio.run(session("loop-b")) == 1


def test_rewind_files_flag_end_to_end(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv(ENABLE_SDK_FILE_CHECKPOINTING_ENV, "1")
    ws = LocalWorkspace(str(tmp_path / "thread" / "project"))
    target = Path(ws.root) / "a.py"
    turns = [[call("WriteFile", "w1", file_path="a.py", content="v1\n")], [AIMessage(content="written")]]

    async def session() -> dict[str, str]:
        from app.cli.print import session_run_id

        async with open_session_run(run_id=session_run_id("t1"), plan=FINISHED, workspace=ws,
                                    **options(tmp_path, turns)) as run:
            await submit_prompt(run, "first")
            return user_ids(run.messages)

    ids = asyncio.run(session())
    assert target.exists()
    out: list[str] = []
    err: list[str] = []
    rewind_options = {k: v for k, v in options(tmp_path, []).items()}

    def rewind(message_id: str) -> int:
        return asyncio.run(run_rewind_files("t1", message_id, workspace=ws, write_out=out.append,
                                            write_err=err.append, **rewind_options))

    assert rewind("not-a-message") == 1
    assert err == ["Error: --rewind-files requires a user message UUID, but not-a-message is not a user message "
                   "in this session\n"]
    assert rewind(ids["first"]) == 0
    assert out == [f"Files rewound to state at message {ids['first']}\n"]
    assert not target.exists()
    monkeypatch.delenv(ENABLE_SDK_FILE_CHECKPOINTING_ENV)
    err.clear()
    assert rewind(ids["first"]) == 1
    assert err == ["Error: File rewinding is not enabled.\n"]


def test_the_rewind_files_flag_is_print_mode_and_standalone(monkeypatch: Any, capsys: Any) -> None:
    calls: list[Any] = []

    def fake_rewind(*args: Any, **kwargs: Any) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(entry, "rewind_files_headless", fake_rewind)
    monkeypatch.setattr("app.cli.print.session_workspace", lambda thread_id: f"ws-{thread_id}")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert entry.main(["-p", "--rewind-files", "m1"]) == 1
    assert capsys.readouterr().err == "Error: --rewind-files requires --thread-id\n"
    assert entry.main(["-p", "--thread-id", "t1", "--rewind-files", "m1", "do this"]) == 1
    assert capsys.readouterr().err == (
        "Error: --rewind-files is a standalone operation and cannot be used with a prompt\n")
    assert entry.main(["-p", "--thread-id", "t1", "--rewind-files", "m1"]) == 0
    ((thread_id, message_id, workspace, opts),) = calls
    assert (thread_id, message_id, workspace, opts.headless) == ("t1", "m1", "ws-t1", True)
