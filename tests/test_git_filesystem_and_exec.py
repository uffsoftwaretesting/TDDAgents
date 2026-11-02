"""Tests for app/utils/git/{git,git_filesystem}.py, app/utils/exec_file_no_throw.py, app/utils/fs_operations.py."""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
import time
from pathlib import Path

import pytest

from app.utils.exec_file_no_throw import exec_file_no_throw, exec_file_no_throw_with_cwd
from app.utils.fs_operations import rm
from app.utils.git.git import find_git_root
from app.utils.git.git_filesystem import (
    get_head_for_dir,
    is_safe_ref_name,
    is_valid_git_sha,
    read_git_head,
    resolve_git_dir,
)

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"


def _repo(root: Path, head: str = "ref: refs/heads/main\n") -> Path:
    git_dir = root / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text(head)
    return git_dir


def test_find_git_root_walks_up(tmp_path):
    _repo(tmp_path)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_git_root(str(nested)) == str(tmp_path)


def test_head_from_a_loose_branch_ref(tmp_path):
    git_dir = _repo(tmp_path)
    (git_dir / "refs" / "heads" / "main").write_text(SHA + "\n")
    assert asyncio.run(get_head_for_dir(str(tmp_path))) == SHA


def test_head_from_packed_refs(tmp_path):
    git_dir = _repo(tmp_path)
    (git_dir / "packed-refs").write_text(f"# pack-refs with: peeled\n{OTHER} refs/heads/dev\n{SHA} refs/heads/main\n"
                                         f"^{OTHER}\n")
    assert asyncio.run(get_head_for_dir(str(tmp_path))) == SHA


def test_detached_head_and_tampered_heads(tmp_path):
    _repo(tmp_path, head=SHA + "\n")
    assert asyncio.run(get_head_for_dir(str(tmp_path))) == SHA
    (tmp_path / ".git" / "HEAD").write_text("$(rm -rf /)\n")
    assert asyncio.run(read_git_head(str(tmp_path / ".git"))) is None
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/../../etc\n")
    assert asyncio.run(read_git_head(str(tmp_path / ".git"))) is None


def test_worktree_gitdir_pointer_and_common_dir(tmp_path):
    main = tmp_path / "main"
    main_git = _repo(main)
    (main_git / "refs" / "heads" / "feature").write_text(SHA + "\n")
    wt_git = main_git / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/feature\n")
    (wt_git / "commondir").write_text("../..\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_git}\n")
    assert asyncio.run(resolve_git_dir(str(worktree))) == str(wt_git)
    assert asyncio.run(get_head_for_dir(str(worktree))) == SHA


def test_an_unborn_branch_has_no_head(tmp_path):
    _repo(tmp_path)
    assert asyncio.run(get_head_for_dir(str(tmp_path))) is None


@pytest.mark.parametrize(("name", "ok"), [
    ("main", True), ("feature/x-1.2+b@c", True), ("-x", False), ("/x", False), ("a..b", False),
    ("a//b", False), ("a/./b", False), ("a b", False), ("", False),
])
def test_safe_ref_names(name, ok):
    assert is_safe_ref_name(name) is ok


def test_valid_shas():
    assert is_valid_git_sha(SHA)
    assert is_valid_git_sha("a" * 64)
    assert not is_valid_git_sha(SHA[:12])
    assert not is_valid_git_sha(SHA.upper())


# ── exec ──────────────────────────────────────────────────────────────────────

def test_exec_success_and_failure(tmp_path):
    ok = asyncio.run(exec_file_no_throw_with_cwd(sys.executable, ["-c", "import os; print(os.getcwd())"],
                                                 cwd=str(tmp_path)))
    assert (ok.code, ok.stdout.strip(), ok.error) == (0, str(tmp_path), None)
    bad = asyncio.run(exec_file_no_throw(sys.executable, ["-c", "import sys; print('o'); sys.exit(3)"]))
    assert bad.code == 3 and bad.stdout.strip() == "o"
    assert bad.error is not None and bad.error.startswith("Command failed with exit code 3:")
    quiet = asyncio.run(exec_file_no_throw(sys.executable, ["-c", "print('o'); exit(2)"],
                                           preserve_output_on_error=False))
    assert (quiet.code, quiet.stdout) == (2, "")


def test_exec_input_env_timeout_and_missing_program():
    script = "import sys,os; print(sys.stdin.read()+os.environ['X'])"
    echoed = asyncio.run(exec_file_no_throw(sys.executable, ["-c", script], input="in-", env={"X": "env"}))
    assert echoed.stdout.strip() == "in-env"
    slow = asyncio.run(exec_file_no_throw(sys.executable, ["-c", "import time; time.sleep(5)"], timeout_ms=200))
    assert slow.code != 0 and slow.error is not None and "timed out after 200 milliseconds" in slow.error
    missing = asyncio.run(exec_file_no_throw("/definitely/not/here", []))
    assert (missing.code, missing.stdout, missing.stderr, missing.error) == (
        1, "", "", "Command failed with ENOENT: /definitely/not/here"
    )


def test_exec_timeout_returns_at_once_with_the_output_read_so_far():
    # The shell's background child keeps the pipes open after the shell itself is killed.
    script = "echo out; echo err >&2; sleep 30 & wait"
    started = time.monotonic()
    result = asyncio.run(exec_file_no_throw("sh", ["-c", script], timeout_ms=500))
    assert time.monotonic() - started < 10
    assert (result.code, result.stdout, result.stderr) == (1, "out", "err")
    assert result.error == f"Command timed out after 500 milliseconds: sh -c {shlex.quote(script)}"


def test_exec_strips_one_final_newline_extends_the_environment_and_names_signals():
    lines = asyncio.run(exec_file_no_throw("printf", ["a\\n\\n"]))
    assert lines.stdout == "a\n"
    crlf = asyncio.run(exec_file_no_throw("printf", ["a\\r\\n"]))
    assert crlf.stdout == "a"
    env = asyncio.run(exec_file_no_throw("sh", ["-c", 'printf "%s:%s" "$TDD_X" "$HOME"'], env={"TDD_X": "1"}))
    assert env.stdout == f"1:{os.environ['HOME']}"
    killed = asyncio.run(exec_file_no_throw("sh", ["-c", "kill -TERM $$"]))
    assert (killed.code, killed.error) == (1, "Command was killed with SIGTERM: sh -c 'kill -TERM $$'")
    quiet = asyncio.run(exec_file_no_throw("/definitely/not/here", [], preserve_output_on_error=False))
    assert (quiet.code, quiet.error) == (1, None)
    no_dir = asyncio.run(exec_file_no_throw_with_cwd("sh", ["-c", "true"], cwd="/definitely/not/a/dir"))
    assert (no_dir.code, no_dir.error) == (1, "Command failed with ENOENT: sh -c true")


def test_exec_in_a_deleted_working_directory_still_runs(tmp_path, monkeypatch):
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    result = asyncio.run(exec_file_no_throw("sh", ["-c", "echo ok"]))
    assert (result.code, result.stdout) == (0, "ok")


# ── fs.rm ──────────────────────────────────────────────────────────────────────

def test_rm_removes_files_links_and_trees_with_node_semantics(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep").write_text("x")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    rm(str(link), recursive=True, force=True)
    assert not link.exists() and (target / "keep").exists()
    gitfile = tmp_path / "gitfile"
    gitfile.write_text("gitdir: /elsewhere")
    rm(str(gitfile), recursive=True, force=True)
    assert not gitfile.exists()
    rm(str(tmp_path / "absent"), recursive=True, force=True)
    with pytest.raises(FileNotFoundError):
        rm(str(tmp_path / "absent"))
    with pytest.raises(IsADirectoryError):
        rm(str(target))
    rm(str(target), recursive=True)
    assert not target.exists()
