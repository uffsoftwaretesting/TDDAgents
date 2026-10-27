"""Phase 5: the nested-memory triggers ReadFile feeds (`FileReadTool`:
`context.nestedMemoryAttachmentTriggers?.add(fullFilePath)`)."""

from __future__ import annotations

import asyncio

from app.state.app_state_store import AppStateStore
from app.tool import tool_context_for
from app.tools.file_read_tool.file_read_tool import build_read_file_tool
from tests.conftest import FakeWorkspace


def test_a_context_starts_with_empty_trigger_and_loaded_sets():
    ctx = tool_context_for(AppStateStore())
    assert ctx.nested_memory_attachment_triggers == set()
    assert ctx.loaded_nested_memory_paths == set()


def test_reading_a_file_queues_it_as_a_nested_memory_trigger():
    ws = FakeWorkspace(kind="local", files={"src/pkg/mod.py": "x = 1\n"})
    ctx = tool_context_for(AppStateStore(), workspace=ws)
    result = asyncio.run(build_read_file_tool().call({"file_path": "./src/pkg/mod.py"}, ctx))
    assert result.is_error is False
    assert ctx.nested_memory_attachment_triggers == {"src/pkg/mod.py"}


def test_a_failed_read_queues_nothing():
    ctx = tool_context_for(AppStateStore(), workspace=FakeWorkspace(kind="local"))
    asyncio.run(build_read_file_tool().call({"file_path": "missing.py"}, ctx))
    assert ctx.nested_memory_attachment_triggers == set()
