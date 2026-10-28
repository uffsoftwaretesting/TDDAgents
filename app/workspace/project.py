"""
A session's project folder (Phase 13): its own folder outside TDDAgents', with its settings.

Claude-code works in the folder it is opened in, with that folder's `.claude/settings.json` as
the project settings. It never runs `git init` and never commits on its own: whether the
project is a git repository is the user's (and the model's) business, and the environment
section says which it is (`Is a git repository`). TDDAgents creates the folder for each
session, outside its own repository (`app/workspace/local.py::workspaces_root`), and only:

* seeds `.tddagents/settings.json` from TDDAgents' shipped defaults (acceptEdits, the pytest and
  pip allow rules), only if the project has none, so each project can be adjusted on its own
  (user decision, 2026-10-28).
"""

from __future__ import annotations

import shutil
from pathlib import Path

#: TDDAgents' shipped project settings, the seed of every new project's.
SHIPPED_SETTINGS = Path(__file__).resolve().parents[2] / ".tddagents" / "settings.json"

PROJECT_SETTINGS = Path(".tddagents") / "settings.json"


def prepare_project(project_dir: Path | str, shipped_settings: Path = SHIPPED_SETTINGS) -> Path:
    """Create `project_dir` and seed its settings if it has none. Idempotent."""
    root = Path(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    settings = root / PROJECT_SETTINGS
    if not settings.exists() and shipped_settings.is_file():
        settings.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(shipped_settings, settings)
    return root


__all__ = ["PROJECT_SETTINGS", "SHIPPED_SETTINGS", "prepare_project"]
