"""
Bundled skill reference files are extracted per process and readable without a prompt (2026-10-29).

A CPF live run asked the user twice to approve reading TDDAgents' own skill references, which
upstream never asks about: `skills/bundledSkills.ts` extracts them under `getBundledSkillsRoot()`
and `checkReadableInternalPath` allows that subtree ("Bundled skill reference files are allowed
for reading", `utils/permissions/filesystem.ts`).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.skills.bundled_skills import (
    bundled_skill_files,
    clear_extracted_bundled_skills,
    extract_bundled_skill_files,
    get_bundled_skill_extract_dir,
    resolve_skill_file_path,
    write_skill_files,
)
from app.types.permissions import PermissionBehavior
from app.utils.permissions.filesystem import (
    check_readable_internal_path,
    get_bundled_skills_root,
    get_tddagents_temp_dir,
)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_TMPDIR", str(tmp_path))
    get_bundled_skills_root.cache_clear()
    clear_extracted_bundled_skills()
    yield get_bundled_skills_root()
    get_bundled_skills_root.cache_clear()
    clear_extracted_bundled_skills()


def _skill(tmp_path: Path) -> Path:
    source = tmp_path / "src" / "my-skill"
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: my-skill\n---\nBody\n")
    (source / "references" / "a.md").write_text("A")
    (source / "references" / "deep").mkdir()
    (source / "references" / "deep" / "b.md").write_text("B")
    return source


def test_the_root_is_per_user_versioned_and_carries_a_per_process_nonce(root, tmp_path):
    from app.services.mcp.client import client_version

    temp = os.path.realpath(tmp_path)
    assert get_tddagents_temp_dir() == os.path.join(temp, f"tddagents-{os.getuid()}")
    parent, nonce = os.path.split(root)
    assert parent == os.path.join(temp, f"tddagents-{os.getuid()}", "bundled-skills", client_version())
    assert len(nonce) == 32 and int(nonce, 16) >= 0
    assert get_bundled_skills_root() == root  # memoized for the life of the process
    get_bundled_skills_root.cache_clear()
    assert get_bundled_skills_root() != root  # a new process gets a new nonce


def test_the_temp_dir_defaults_to_tmp(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_TMPDIR", raising=False)
    assert get_tddagents_temp_dir() == os.path.join(os.path.realpath("/tmp"), f"tddagents-{os.getuid()}")


def test_the_reference_files_are_everything_but_the_prompt(tmp_path):
    assert bundled_skill_files(str(_skill(tmp_path))) == {"references/a.md": "A", "references/deep/b.md": "B"}


def test_extraction_writes_owner_only_once_per_process(root, tmp_path):
    source = _skill(tmp_path)
    directory = extract_bundled_skill_files("my-skill", str(source))
    assert directory == get_bundled_skill_extract_dir("my-skill") == os.path.join(root, "my-skill")
    written = Path(directory) / "references" / "deep" / "b.md"
    assert written.read_text() == "B" and not (Path(directory) / "SKILL.md").exists()
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert stat.S_IMODE((Path(directory) / "references").stat().st_mode) == 0o700
    # Extract once: a second call is the memoized result, even if the source changed.
    (source / "references" / "a.md").write_text("changed")
    assert extract_bundled_skill_files("my-skill", str(source)) == directory
    assert (Path(directory) / "references" / "a.md").read_text() == "A"


def test_a_failed_extraction_returns_none_and_is_remembered(root, tmp_path):
    source = _skill(tmp_path)
    blocker = Path(get_bundled_skill_extract_dir("my-skill"))
    blocker.parent.mkdir(parents=True)
    blocker.write_text("a file where the directory should be")
    assert extract_bundled_skill_files("my-skill", str(source)) is None
    assert extract_bundled_skill_files("my-skill", str(source)) is None


def test_files_are_never_written_through_an_existing_path(root, tmp_path):
    """`safeWriteFile`: O_EXCL | O_NOFOLLOW, and no unlink-and-retry."""
    target = tmp_path / "out"
    target.mkdir()
    (target / "a.md").symlink_to(tmp_path / "elsewhere.md")
    with pytest.raises(OSError):
        write_skill_files(str(target), {"a.md": "x"})
    assert not (tmp_path / "elsewhere.md").exists()


@pytest.mark.parametrize("rel", ["../x.md", "a/../../x.md", "/etc/x.md"])
def test_a_path_escaping_the_skill_dir_is_refused(rel):
    with pytest.raises(ValueError, match="escapes skill dir"):
        resolve_skill_file_path("/base", rel)
    assert resolve_skill_file_path("/base", "references/a.md") == "/base/references/a.md"


def test_reads_under_the_root_are_allowed_without_a_prompt(root):
    inside = os.path.join(root, "my-skill", "references", "a.md")
    allowed = check_readable_internal_path(inside, {"file_path": inside}, "/project")
    assert allowed.behavior == PermissionBehavior.ALLOW
    assert allowed.decision_reason == {
        "type": "other", "reason": "Bundled skill reference files are allowed for reading"}
    for outside in (root, root + "-other/a.md", os.path.join(os.path.dirname(root), "guessed", "a.md")):
        assert check_readable_internal_path(outside, {}, "/project").behavior == PermissionBehavior.PASSTHROUGH


def test_read_file_reads_an_extracted_reference_without_asking(root, tmp_path):
    """The live case: ReadFile on a launched skill's reference file, in default mode, outside the project."""
    import asyncio

    from app.state.app_state_store import AppState, AppStateStore
    from app.tool import tool_context_for
    from app.tools.file_read_tool.file_read_tool import build_read_file_tool
    from app.workspace.local import LocalWorkspace

    directory = extract_bundled_skill_files("my-skill", str(_skill(tmp_path)))
    assert directory is not None
    target = os.path.join(directory, "references", "a.md")
    ctx = tool_context_for(AppStateStore(AppState()), workspace=LocalWorkspace(str(tmp_path / "project")))
    tool = build_read_file_tool()
    verdict = asyncio.run(tool.check_permissions({"file_path": target}, ctx))
    assert verdict.behavior == PermissionBehavior.ALLOW
    assert asyncio.run(tool.call({"file_path": target}, ctx)).content == "1\tA"
