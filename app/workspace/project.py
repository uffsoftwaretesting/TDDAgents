"""
A session's project folder (Phase 13): its own folder outside TDDAgents', with its settings.

Claude-code works in the folder it is opened in, with that folder's `.claude/settings.json` as
the project settings. It never runs `git init` and never commits on its own: whether the
project is a git repository is the user's (and the model's) business, and the environment
section says which it is (`Is a git repository`). TDDAgents creates the folder for each
session, outside its own repository (`app/workspace/local.py::workspaces_root`), and only:
* seeds `.tddagents/settings.json` from TDDAgents' shipped defaults (`acceptEdits`, and no allow
  rules since 2026-11-03, as claude-code ships none), only if the project has none, so each
  project can be adjusted on its own (user decision, 2026-10-28). The defaults carry no `sandbox`
  block, so commands run without one, as upstream's default (`sandbox.enabled` is `false` unless set;
  user decision 2026-11-20, which reverses 2026-11-08's default). The sandbox is opt-in: `--sandbox`
  applies the profile in `.tddagents/settings.sandbox.json` (`app/utils/sandbox/`): required
  (`failIfUnavailable`), never bypassed (`allowUnsandboxedCommands: false`), a private home with the
  project readable, `project.json` read-only to commands, and the package registries allowed. A project
  seeded earlier keeps the settings it was seeded with.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

#: TDDAgents' shipped project settings, the seed of every new project's.
SHIPPED_SETTINGS = Path(__file__).resolve().parents[2] / ".tddagents" / "settings.json"

#: The sandbox's settings (`sandbox.enabled` and the rules around it), applied by `--sandbox`.
SHIPPED_SANDBOX_PROFILE = SHIPPED_SETTINGS.with_name("settings.sandbox.json")

PROJECT_SETTINGS = Path(".tddagents") / "settings.json"


def load_sandbox_profile(path: Path = SHIPPED_SANDBOX_PROFILE) -> dict[str, Any]:
    """The sandbox profile's settings document (`{"sandbox": {...}}`)."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} is not a settings object")
    return document


def prepare_project(project_dir: Path | str, shipped_settings: Path = SHIPPED_SETTINGS) -> Path:
    """Create `project_dir` and seed its settings if it has none. Idempotent."""
    root = Path(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    settings = root / PROJECT_SETTINGS
    if not settings.exists() and shipped_settings.is_file():
        settings.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(shipped_settings, settings)
    return root


__all__ = ["PROJECT_SETTINGS", "SHIPPED_SANDBOX_PROFILE", "SHIPPED_SETTINGS", "load_sandbox_profile",
           "prepare_project"]
