"""`--continue`, `--resume`, `--session-id` and `--fork-session` (`main.tsx`, `print.ts`)."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

import app.main as entry
from app.cli.print import (
    NoConversationFound,
    fork_thread,
    most_recent_thread,
    session_run_id,
    thread_transcript,
)
from app.tdd.plan import plan_path
from app.utils.plans import PLAN_SLUG_FILE_NAME
from app.utils.session_storage_portable import get_project_dir
from app.workspace.local import LocalWorkspace


@pytest.fixture
def workspaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspaces"
    monkeypatch.setenv("TDDAGENTS_WORKSPACES_DIR", str(root))
    return root


def make_session(thread: str, *, request: str = "", mtime: float | None = None) -> Path:
    workspace = LocalWorkspace.for_run(thread)
    transcript = thread_transcript(thread)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps({"type": "user", "sessionId": session_run_id(thread), "n": 1}) + "\n\n"
                          + json.dumps({"type": "summary"}) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(transcript, (mtime, mtime))
    if request:
        reports = workspace.run_dir / "metrics_and_logging"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "initial_user_prompt.txt").write_text(f"INITIAL PROMPT SENT BY THE USER\n{'=' * 80}\n{request}\n"
                                                         f"{'=' * 80}\n", encoding="utf-8")
    return workspace.root


def test_continue_takes_the_most_recent_thread(workspaces: Path) -> None:
    assert most_recent_thread() is None
    now = time.time()
    make_session("tdd-old", mtime=now - 100)
    make_session("tdd-new", mtime=now)
    (workspaces / "tdd-empty" / "project").mkdir(parents=True)
    (workspaces / "not-a-session").mkdir()
    assert most_recent_thread() == "tdd-new"


def test_a_fork_shares_the_project_and_copies_the_session(workspaces: Path, tmp_path: Path) -> None:
    project = make_session("tdd-src")
    (project / "calc.py").write_text("x = 1\n")
    old_run = session_run_id("tdd-src")
    plan_path(old_run, str(project)).parent.mkdir(parents=True, exist_ok=True)
    plan_path(old_run, str(project)).write_text('{"items": []}', encoding="utf-8")
    sessions = get_project_dir(str(project))
    (sessions / old_run / PLAN_SLUG_FILE_NAME).write_text("old-slug", encoding="utf-8")
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "old-slug.md").write_text("# The plan\n", encoding="utf-8")

    fork_thread("tdd-src", "tdd-fork", plans_dir=plans)
    fork = LocalWorkspace.for_run("tdd-fork")
    assert fork.root == project and (fork.root / "calc.py").exists()
    assert fork.run_dir == workspaces / "tdd-fork"
    new_run = session_run_id("tdd-fork")
    entries = [json.loads(line) for line in thread_transcript("tdd-fork").read_text().splitlines()]
    assert entries == [{"type": "user", "sessionId": new_run, "n": 1}, {"type": "summary"}]
    assert plan_path(new_run, str(project)).read_text() == '{"items": []}'
    new_slug = (sessions / new_run / PLAN_SLUG_FILE_NAME).read_text()
    assert new_slug != "old-slug" and (plans / f"{new_slug}.md").read_text() == "# The plan\n"
    with pytest.raises(NoConversationFound, match="No conversation found with session ID: tdd-none"):
        fork_thread("tdd-none", "tdd-x")


def flags(*argv: str) -> Any:
    return entry.parse_args(list(argv))


def test_session_flags_resolve_the_thread(workspaces: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_session("tdd-a", request="Build a calculator", mtime=time.time() - 50)
    make_session("tdd-b", request="Write a parser", mtime=time.time())
    assert entry.resolve_session_flags(flags()) is None
    assert entry.resolve_session_flags(flags("-c")) == "tdd-b"
    assert entry.resolve_session_flags(flags("--resume", "tdd-a")) == "tdd-a"
    session = str(uuid.uuid4())
    assert entry.resolve_session_flags(flags("--session-id", session)) == session
    forked = entry.resolve_session_flags(flags("-c", "--fork-session", "--session-id", session))
    assert forked == session and LocalWorkspace.for_run(session).root == LocalWorkspace.for_run("tdd-b").root
    fresh = entry.resolve_session_flags(flags("-r", "tdd-a", "--fork-session"))
    assert fresh is not None and fresh.startswith("tdd-") and fresh not in ("tdd-a", "tdd-b")


@pytest.mark.parametrize(("argv", "error"), [
    (("-c", "--session-id", "x"), "Error: --session-id can only be used with --continue or --resume if "
                                  "--fork-session is also specified."),
    (("--session-id", "not-a-uuid"), "Error: Invalid session ID. Must be a valid UUID."),
    (("-p", "--resume", "tdd-missing"), "No conversation found with session ID: tdd-missing"),
    (("-p", "--resume"), "Error: --resume requires a session ID when used with --print"),
])
def test_session_flag_errors(workspaces: Path, argv: tuple[str, ...], error: str) -> None:
    with pytest.raises(entry.SessionFlagError) as raised:
        entry.resolve_session_flags(flags(*argv))
    assert str(raised.value) == error


def test_a_used_session_id_and_continue_without_sessions(workspaces: Path) -> None:
    session = str(uuid.uuid4())
    make_session(session)
    with pytest.raises(entry.SessionFlagError, match=f"Error: Session ID {session} is already in use."):
        entry.resolve_session_flags(flags("--session-id", session))
    for thread in list(workspaces.iterdir()):
        for transcript in [thread_transcript(thread.name)]:
            transcript.unlink()
    with pytest.raises(entry.SessionFlagError, match="No conversation found to continue"):
        entry.resolve_session_flags(flags("-c"))


def test_the_resume_picker(workspaces: Path, monkeypatch: pytest.MonkeyPatch,
                           capsys: pytest.CaptureFixture[str]) -> None:
    make_session("tdd-a", request="Build a calculator", mtime=time.time() - 50)
    make_session("tdd-b", request="Write a parser", mtime=time.time())
    answers = iter(["9", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert entry.resolve_session_flags(flags("--resume")) == "tdd-a"
    out = capsys.readouterr().out
    assert "1. tdd-b  Write a parser" in out and "2. tdd-a  Build a calculator" in out
    assert "❌ Invalid option. Try again." in out
    monkeypatch.setattr("builtins.input", lambda prompt: "1")
    assert entry.resolve_session_flags(flags("--resume", "calc")) == "tdd-a"
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert entry.resolve_session_flags(flags("--resume")) is None
    assert entry.resolve_session_flags(flags("--resume", "nothing-matches")) is None
    assert "No conversations found to resume." in capsys.readouterr().out


def test_main_runs_the_resolved_thread(workspaces: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["-p", "--resume", "tdd-missing", "hi"]) == 1
    assert "No conversation found with session ID: tdd-missing" in capsys.readouterr().err
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    make_session("tdd-a")
    assert entry.main(["--resume"]) == 0
