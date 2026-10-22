"""
Phase 5: the `nested_memory` attachment source, offline.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getNestedMemoryAttachments`,
`getNestedMemoryAttachmentsForFile`, `getDirectoriesToProcess`, `memoryFilesToAttachments`, and
`src/utils/claudemd.ts` -> `getMemoryFilesForNestedDirectory`,
`getConditionalRulesForCwdLevelDirectory`, `getManagedAndUserConditionalRules`,
`processConditionedMdRules`.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.loop.attachments.nested_memory import (
    NestedMemoryState,
    get_directories_to_process,
    get_nested_memory_attachments,
)
from app.loop.context import AppStateStore, tool_context_for
from app.loop.context.file_state import FileState
from tests.conftest import FakeWorkspace


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def ctx(files: dict[str, str], triggers: set[str], tmp_path: Path) -> tuple[Any, NestedMemoryState]:
    state = NestedMemoryState(triggers=set(triggers))
    context = tool_context_for(AppStateStore(), workspace=FakeWorkspace(kind="local", files=files))
    return replace(context), state


def test_directories_to_process():
    assert get_directories_to_process("src/pkg/a.py") == (["src", "src/pkg"], [""])
    assert get_directories_to_process("a.py") == ([], [""])


def test_a_nested_tddagents_md_is_attached_once(tmp_path):
    files = {"src/TDDAGENTS.md": "src rules", "src/a.py": "x"}
    context, state = ctx(files, {"src/a.py"}, tmp_path)
    got = run(get_nested_memory_attachments(context, state, home=str(tmp_path), managed_dir=str(tmp_path / "etc")))
    assert [(a.type, a.data["path"], a.data["content"]) for a in got] == [
        ("nested_memory", "src/TDDAGENTS.md", "src rules")
    ]
    assert state.triggers == set()
    assert "src/TDDAGENTS.md" in state.loaded_paths
    assert context.read_file_state["src/TDDAGENTS.md"] == FileState(content="src rules")
    state.triggers.add("src/a.py")
    assert run(get_nested_memory_attachments(context, state, home=str(tmp_path), managed_dir=str(tmp_path / "etc"))) \
        == []


def test_nested_local_and_dot_dir_files_in_upstream_order(tmp_path):
    files = {
        "pkg/TDDAGENTS.md": "p",
        "pkg/.tddagents/TDDAGENTS.md": "dot",
        "pkg/TDDAGENTS.local.md": "local",
        "pkg/.tddagents/rules/always.md": "always",
        "pkg/.tddagents/rules/py.md": "---\npaths: '*.py'\n---\npy only",
        "pkg/.tddagents/rules/js.md": "---\npaths: '*.js'\n---\njs only",
    }
    context, state = ctx(files, {"pkg/m.py"}, tmp_path)
    got = run(get_nested_memory_attachments(context, state, home=str(tmp_path), managed_dir=str(tmp_path / "etc")))
    assert [a.data["path"] for a in got] == [
        "pkg/TDDAGENTS.md", "pkg/.tddagents/TDDAGENTS.md", "pkg/TDDAGENTS.local.md",
        "pkg/.tddagents/rules/always.md", "pkg/.tddagents/rules/py.md",
    ]


def test_root_conditional_rules_match_the_target(tmp_path):
    files = {".tddagents/rules/tests.md": "---\npaths: 'tests/**'\n---\ntest rules", "tests/test_a.py": ""}
    context, state = ctx(files, {"tests/test_a.py"}, tmp_path)
    got = run(get_nested_memory_attachments(context, state, home=str(tmp_path), managed_dir=str(tmp_path / "etc")))
    assert [a.data["path"] for a in got] == [".tddagents/rules/tests.md"]
    context, state = ctx(files, {"src/a.py"}, tmp_path)
    assert run(get_nested_memory_attachments(context, state, home=str(tmp_path), managed_dir=str(tmp_path / "etc"))) \
        == []


def test_user_conditional_rules_are_matched_against_the_workspace_path(tmp_path):
    rules = tmp_path / ".tddagents" / "rules"
    rules.mkdir(parents=True)
    (rules / "py.md").write_text("---\npaths: '**/*.py'\n---\nuser py rule")
    context, state = ctx({"src/a.py": ""}, {"src/a.py"}, tmp_path)
    got = run(get_nested_memory_attachments(context, state, home=str(tmp_path), managed_dir=str(tmp_path / "etc")))
    assert [a.data["content"] for a in got] == ["user py rule"]


def test_a_file_already_read_is_not_attached(tmp_path):
    files = {"src/TDDAGENTS.md": "src rules"}
    context, state = ctx(files, {"src/a.py"}, tmp_path)
    context.read_file_state["src/TDDAGENTS.md"] = FileState(content="src rules")
    assert run(get_nested_memory_attachments(context, state, home=str(tmp_path), managed_dir=str(tmp_path / "etc"))) \
        == []


def test_no_triggers_or_no_workspace_or_outside_paths(tmp_path):
    context, state = ctx({}, set(), tmp_path)
    assert run(get_nested_memory_attachments(context, state, home=str(tmp_path))) == []
    bare = tool_context_for(AppStateStore())
    assert run(get_nested_memory_attachments(bare, NestedMemoryState(triggers={"a.py"}), home=str(tmp_path))) == []
    context, state = ctx({}, {"/etc/passwd", "../x.py"}, tmp_path)
    assert run(get_nested_memory_attachments(context, state, home=str(tmp_path), managed_dir=str(tmp_path / "e"))) \
        == []
