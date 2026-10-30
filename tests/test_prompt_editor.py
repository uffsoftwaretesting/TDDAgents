"""`app/utils/prompt_editor.py`: upstream's `editFileInEditor` / `getExternalEditor`, with real editors."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from app.utils import prompt_editor
from app.utils.prompt_editor import (
    EDITOR_OVERRIDES,
    FALLBACK_EDITORS,
    EditorResult,
    edit_file_in_editor,
    get_external_editor,
)


def test_upstreams_tables():
    assert EDITOR_OVERRIDES == {"code": "code -w", "subl": "subl --wait"}
    assert FALLBACK_EDITORS == ("code", "vi", "nano")


def test_visual_then_editor_then_the_first_fallback_on_path(monkeypatch):
    monkeypatch.setenv("VISUAL", " vim ")
    monkeypatch.setenv("EDITOR", "nano")
    assert get_external_editor(lambda name: None) == "vim"
    monkeypatch.setenv("VISUAL", "  ")
    assert get_external_editor(lambda name: None) == "nano"
    monkeypatch.delenv("VISUAL")
    monkeypatch.delenv("EDITOR")
    assert get_external_editor(lambda name: f"/bin/{name}" if name == "vi" else None) == "vi"
    assert get_external_editor(lambda name: None) is None


def _script(tmp_path: Path, body: str) -> str:
    script = tmp_path / "fake-editor"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def test_the_editor_runs_on_the_file_and_its_edit_is_read_back(tmp_path):
    draft = tmp_path / "plan draft.md"
    draft.write_text("Setup\n", encoding="utf-8")
    editor = _script(tmp_path, 'echo "Docker" >> "$1"')
    assert edit_file_in_editor(draft, editor) == EditorResult("Setup\nDocker\n")


def test_an_editor_that_fails_is_reported(tmp_path):
    draft = tmp_path / "d.md"
    draft.write_text("x", encoding="utf-8")
    editor = _script(tmp_path, "exit 3")
    assert edit_file_in_editor(draft, editor) == EditorResult(None, f"{editor} exited with code 3")


def test_no_editor_or_no_file_reads_nothing(tmp_path, monkeypatch):
    draft = tmp_path / "d.md"
    draft.write_text("x", encoding="utf-8")
    monkeypatch.setattr(prompt_editor, "get_external_editor", lambda: None)
    assert edit_file_in_editor(draft) == EditorResult(None)
    assert edit_file_in_editor(tmp_path / "missing.md", "vi") == EditorResult(None)


@pytest.mark.parametrize(("editor", "command"), [("code", "code -w"), ("subl", "subl --wait"), ("vim -n", "vim -n")])
def test_gui_editors_are_told_to_wait(tmp_path, monkeypatch, editor: str, command: str):
    draft = tmp_path / "d.md"
    draft.write_text("x", encoding="utf-8")
    calls: list[tuple[str, bool]] = []

    def call(cmd: str, shell: bool) -> int:
        calls.append((cmd, shell))
        return 0

    monkeypatch.setattr("app.utils.prompt_editor.subprocess.call", call)
    assert edit_file_in_editor(draft, editor) == EditorResult("x")
    assert calls == [(f'{command} "{draft}"', True)]


def test_the_default_editor_is_looked_up(tmp_path, monkeypatch):
    draft = tmp_path / "d.md"
    draft.write_text("x", encoding="utf-8")
    monkeypatch.setattr(prompt_editor, "get_external_editor", lambda: _script(tmp_path, 'echo y > "$1"'))
    assert edit_file_in_editor(draft) == EditorResult("y\n")
