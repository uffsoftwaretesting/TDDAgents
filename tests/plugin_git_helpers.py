"""Offline git fixtures for the plugin tests: a local repository with commits, cloned over file://."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def run_git(cwd: Path, *args: str) -> str:
    """Run git in `cwd` with a fixed identity and no user config; return stdout."""
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def make_repo(root: Path, files: dict[str, str], *, branch: str = "main") -> str:
    """Create a repository at `root` holding `files`, committed once; return the commit SHA."""
    root.mkdir(parents=True, exist_ok=True)
    run_git(root, "init", "-q", "-b", branch)
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "init")
    return run_git(root, "rev-parse", "HEAD")


def commit_more(root: Path, files: dict[str, str]) -> str:
    """Add another commit to the repository at `root`; return its SHA."""
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "more")
    return run_git(root, "rev-parse", "HEAD")


def manifest(name: str, **extra: object) -> str:
    """A plugin.json body."""
    return json.dumps({"name": name, **extra})
