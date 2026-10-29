"""
The read tools read outside the project once permission allows it (2026-10-28, user decision).

A live run asked to read `~/.cache/.../rules.png`: the gate asked, the user said yes, and the
workspace then refused the path as "outside the project". Upstream's Read, Glob and Grep read
any host path the permission check allows; writes stay bounded to the project.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.state.app_state_store import AppState, AppStateStore
from app.tool import tool_context_for
from app.tools.file_read_tool.file_read_tool import build_read_file_tool
from app.tools.file_write_tool.file_write_tool import build_write_file_tool
from app.tools.glob_tool.glob_tool import build_glob_tool
from app.tools.grep_tool.grep_tool import build_grep_tool
from app.tools.utils import (
    outside_host_path,
    path_kind,
    read_host_path,
    read_state_key,
    search_target,
)
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Any, Path]:
    ws = LocalWorkspace(str(tmp_path / "project"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    ws.write_file("inside.txt", "inside\n")
    return ws, outside


def ctx(ws: Any) -> Any:
    return tool_context_for(AppStateStore(AppState()), workspace=ws, run_id="r1")


def test_outside_host_path_is_an_absolute_path_beyond_the_root(layout):
    ws, outside = layout
    assert outside_host_path(ws, str(outside / "notes.txt")) == outside / "notes.txt"
    assert outside_host_path(ws, f"  {outside}/x/../notes.txt ") == outside / "notes.txt"
    assert outside_host_path(ws, str(ws.root / "inside.txt")) is None
    assert outside_host_path(ws, str(ws.root)) is None
    assert outside_host_path(ws, "inside.txt") is None
    # A workspace with no host root (E2B) stays bounded.
    assert outside_host_path(FakeWorkspace(), str(outside / "notes.txt")) is None


def test_outside_host_path_expands_home(monkeypatch, layout):
    ws, outside = layout
    monkeypatch.setenv("HOME", str(outside))
    assert outside_host_path(ws, "~/notes.txt") == outside / "notes.txt"


def test_search_target_and_path_kind(layout):
    ws, outside = layout
    assert search_target(ws, str(outside)) == str(outside)
    assert search_target(ws, str(ws.root / "sub")) == "sub"
    assert (path_kind(ws, str(outside)), path_kind(ws, str(outside / "notes.txt"))) == ("dir", "file")
    assert path_kind(ws, str(outside / "missing")) is None


def test_read_host_path_and_its_state_key(layout):
    ws, outside = layout
    context = ctx(ws)
    target = str(outside / "notes.txt")
    assert read_host_path(context, target) == outside / "notes.txt"
    assert read_host_path(None, target) is None
    assert read_state_key(context, target) == target
    assert read_state_key(context, "inside.txt") == "inside.txt"


def test_read_file_reads_a_text_file_outside_the_project(layout):
    ws, outside = layout
    context = ctx(ws)
    target = str(outside / "notes.txt")
    res = asyncio.run(build_read_file_tool().call({"file_path": target}, context))
    assert (res.is_error, res.content) == (False, "1\talpha\n2\tbeta")
    assert target in context.read_file_state
    assert context.nested_memory_attachment_triggers == set()


def test_read_file_reads_an_image_outside_the_project(layout):
    from PIL import Image

    ws, outside = layout
    Image.new("RGB", (4, 4), "red").save(outside / "dot.png")
    context = ctx(ws)
    res = asyncio.run(build_read_file_tool().call({"file_path": str(outside / "dot.png")}, context))
    assert res.is_error is False and res.content.startswith(f"Image file read: {outside / 'dot.png'} (image/png")
    assert res.content_blocks[0]["type"] == "image"
    assert context.nested_memory_attachment_triggers == set()


def test_a_missing_outside_image_is_reported_missing(layout):
    ws, outside = layout
    res = asyncio.run(build_read_file_tool().call({"file_path": str(outside / "nope.png")}, ctx(ws)))
    assert res.is_error is True and res.content.startswith("File does not exist.")


def test_glob_and_grep_search_a_directory_outside_the_project(layout):
    ws, outside = layout
    context = ctx(ws)
    glob = asyncio.run(build_glob_tool().call({"pattern": "*.txt", "path": str(outside)}, context))
    assert glob.content == str(outside / "notes.txt")
    glob = asyncio.run(build_glob_tool().call({"pattern": f"{outside}/*.txt"}, context))
    assert glob.content == str(outside / "notes.txt")
    grep = asyncio.run(build_grep_tool().call({"pattern": "beta", "path": str(outside)}, context))
    assert grep.content == f"Found 1 file\n{outside / 'notes.txt'}"
    assert build_grep_tool().validate_input({"pattern": "x", "path": str(outside)}, context).valid is True


def test_writes_stay_inside_the_project(layout):
    ws, outside = layout
    context = ctx(ws)
    target = str(outside / "new.txt")
    res = asyncio.run(build_write_file_tool().call({"file_path": target, "content": "x"}, context))
    assert res.is_error is True
    assert not (outside / "new.txt").exists()
