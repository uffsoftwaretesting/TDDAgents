"""
A session's project folder (Phase 13): its own git working tree, settings and ignore file.

Claude-code works in the folder it is opened in, usually a git repository, with that folder's
`.claude/settings.json` as the project settings. TDDAgents creates that folder for each
session, outside its own repository (`app/workspace/local.py::workspaces_root`):

* `git init`, once. TDDAgents never commits: the model is told upstream's rule (never commit
  unless the user asks) — `app/prompts/system/environment.md`;
* a `.gitignore` for what is not the project's source: the venv, caches, TDDAgents' coverage
  files and the local settings;
* `.tddagents/settings.json` seeded from TDDAgents' shipped defaults (acceptEdits, the pytest
  and pip allow rules), only if the project has none, so each project can be adjusted on its
  own (user decision, 2026-10-28).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("TDDOrchestrator.Project")

#: TDDAgents' shipped project settings, the seed of every new project's.
SHIPPED_SETTINGS = Path(__file__).resolve().parents[2] / ".tddagents" / "settings.json"

PROJECT_SETTINGS = Path(".tddagents") / "settings.json"

GITIGNORE = (
    ".venv/\n"
    "__pycache__/\n"
    ".pytest_cache/\n"
    ".tddagents/coverage/\n"
    ".tddagents/settings.local.json\n"
)


def prepare_project(project_dir: Path | str, shipped_settings: Path = SHIPPED_SETTINGS) -> Path:
    """Make `project_dir` a project: a git working tree with an ignore file and settings. Idempotent."""
    root = Path(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True, text=True)
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE, encoding="utf-8")
    settings = root / PROJECT_SETTINGS
    if not settings.exists() and shipped_settings.is_file():
        settings.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(shipped_settings, settings)
    return root


__all__ = ["GITIGNORE", "PROJECT_SETTINGS", "SHIPPED_SETTINGS", "prepare_project"]
