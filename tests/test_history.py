"""`app/history.py`, the port of `history.ts` (the REPL's ↑ prompt history)."""

from __future__ import annotations

import json
import stat

from app.history import MAX_HISTORY_ITEMS, LogEntry, PromptHistory


def _history(tmp_path, project="/p", session="s1", clock=None, env=None) -> PromptHistory:
    ticks = iter(range(1, 10_000)) if clock is None else clock
    return PromptHistory(project, session, tmp_path / "history.jsonl", now=lambda: next(ticks), env=env or {})


def test_entries_are_appended_as_upstreams_json_lines(tmp_path):
    history = _history(tmp_path)
    history.add_to_history("write the tests")
    (line,) = (tmp_path / "history.jsonl").read_text().splitlines()
    assert json.loads(line) == {"display": "write the tests", "pastedContents": {}, "timestamp": 1000.0,
                                "project": "/p", "sessionId": "s1"}
    assert stat.S_IMODE((tmp_path / "history.jsonl").stat().st_mode) == 0o600


def test_this_project_newest_first_and_this_session_first(tmp_path):
    clock = iter(range(1, 100))
    mine, other_session, other_project = (_history(tmp_path, session="s1", clock=clock),
                                          _history(tmp_path, session="s2", clock=clock),
                                          _history(tmp_path, project="/q", clock=clock))
    mine.add_to_history("a")
    other_session.add_to_history("b")
    other_project.add_to_history("c")
    mine.add_to_history("d")
    other_session.add_to_history("e")
    assert mine.get_history() == ["d", "a", "e", "b"]
    assert other_session.get_history() == ["e", "b", "d", "a"]
    assert other_project.get_history() == ["c"]


def test_the_window_is_the_last_hundred_entries(tmp_path):
    history = _history(tmp_path)
    for i in range(MAX_HISTORY_ITEMS + 5):
        history.add_to_history(f"p{i}")
    got = history.get_history()
    assert len(got) == MAX_HISTORY_ITEMS and got[0] == f"p{MAX_HISTORY_ITEMS + 4}" and got[-1] == "p5"
    other = _history(tmp_path, session="s2")
    other.add_to_history("mine")
    assert other.get_history()[0] == "mine" and len(other.get_history()) == MAX_HISTORY_ITEMS


def test_a_missing_or_damaged_log_reads_what_it_can(tmp_path):
    history = _history(tmp_path)
    assert history.get_history() == []
    (tmp_path / "history.jsonl").write_text('not json\n\n{"display": 3, "project": "/p"}\n'
                                            '{"display": "ok", "project": "/p", "timestamp": 1}\n'
                                            '{"display": "x"}\n')
    assert history.get_history() == ["ok"]


def test_the_skip_variable_and_a_write_failure_store_nothing(tmp_path):
    skipped = _history(tmp_path, env={"TDDAGENTS_SKIP_PROMPT_HISTORY": "1"})
    skipped.add_to_history("secret")
    assert not (tmp_path / "history.jsonl").exists()
    (tmp_path / "blocker").write_text("")
    broken = PromptHistory("/p", "s", tmp_path / "blocker" / "history.jsonl", env={})
    broken.add_to_history("x")
    assert broken.get_history() == []


def test_log_entry_json():
    assert json.loads(LogEntry("é", 2.0, "/p").as_json()) == {
        "display": "é", "pastedContents": {}, "timestamp": 2.0, "project": "/p", "sessionId": None}


def test_the_default_path_is_under_the_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    assert PromptHistory("/p", "s").path == tmp_path / "history.jsonl"
