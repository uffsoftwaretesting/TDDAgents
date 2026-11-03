"""
The project onboarding steps: create an app (an empty folder), or run `/init` (a folder without
`TDDAGENTS.md`), and whether the REPL still shows them as tips.

Ported from `reference/claude-code/src/projectOnboardingState.ts` -> `Step`, `getSteps`,
`isProjectOnboardingComplete`, `maybeMarkProjectOnboardingComplete`, `shouldShowProjectOnboarding`,
`incrementProjectOnboardingSeenCount`, and `components/LogoV2/feedConfigs.tsx` ->
`createProjectOnboardingFeed` (its title and lines, as text: the REPL is line-based).

`hasCompletedProjectOnboarding` and `projectOnboardingSeenCount` live in the project's entry of
the global config (`app/utils/config.py`), as upstream's `ProjectConfig`; a missing count is 0
(`DEFAULT_PROJECT_CONFIG`). Product substitutions: `CLAUDE.md` is `TDDAGENTS.md`, and the steps
and the home-directory note name TDDAgents for Claude.

Divergences:

* Every function takes the project folder (`cwd`): upstream reads the process's `getCwd()`, and a
  TDDAgents process may hold several sessions (as `app/utils/config.py`).
* `shouldShowProjectOnboarding` is not memoized: its one caller, the REPL header, asks once per
  session.
* The seen count is incremented once per REPL session that shows the tips
  (`app/screens/repl.py`): upstream's `LogoV2` increments it once per new version
  (`lastReleaseNotesSeen !== VERSION`), and TDDAgents has no release notes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.utils.claudemd import MEMORY_FILE_NAME
from app.utils.config import ProjectConfig, get_current_project_config, save_current_project_config
from app.utils.file import is_dir_empty

#: `ProjectConfig` keys.
HAS_COMPLETED_PROJECT_ONBOARDING = "hasCompletedProjectOnboarding"
PROJECT_ONBOARDING_SEEN_COUNT = "projectOnboardingSeenCount"

#: `shouldShowProjectOnboarding`: the tips stop once they were seen this many times.
MAX_PROJECT_ONBOARDING_SEEN_COUNT = 4

#: `createProjectOnboardingFeed`'s title.
PROJECT_ONBOARDING_FEED_TITLE = "Tips for getting started"
#: `createProjectOnboardingFeed`'s warning when the session runs in the home directory.
HOME_DIRECTORY_WARNING = (
    "Note: You have launched TDDAgents in your home directory. For the best experience, launch it in a "
    "project directory instead."
)
#: `figures.tick`.
TICK = "✔"


@dataclass(frozen=True)
class Step:
    """`Step`."""

    key: str
    text: str
    is_complete: bool
    is_completable: bool
    is_enabled: bool


def get_steps(cwd: str | Path) -> list[Step]:
    """`getSteps`: create something in an empty folder; otherwise, `/init` until `TDDAGENTS.md` exists."""
    has_claude_md = (Path(cwd) / MEMORY_FILE_NAME).exists()
    is_workspace_dir_empty = is_dir_empty(cwd)
    return [
        Step(key="workspace", text="Ask TDDAgents to create a new app or clone a repository",
             is_complete=False, is_completable=True, is_enabled=is_workspace_dir_empty),
        Step(key="claudemd", text=f"Run /init to create a {MEMORY_FILE_NAME} file with instructions for "
             "TDDAgents", is_complete=has_claude_md, is_completable=True, is_enabled=not is_workspace_dir_empty),
    ]


def is_project_onboarding_complete(cwd: str | Path) -> bool:
    """`isProjectOnboardingComplete`: every completable, enabled step is complete."""
    return all(step.is_complete for step in get_steps(cwd) if step.is_completable and step.is_enabled)


def maybe_mark_project_onboarding_complete(cwd: str | Path) -> None:
    """
    `maybeMarkProjectOnboardingComplete`: record the onboarding as complete once it is. The saved
    flag short-circuits the filesystem check, since the REPL calls this on every prompt.
    """
    if get_current_project_config(cwd).get(HAS_COMPLETED_PROJECT_ONBOARDING):
        return
    if is_project_onboarding_complete(cwd):
        save_current_project_config(cwd, lambda current: {**current, HAS_COMPLETED_PROJECT_ONBOARDING: True})


def _seen_count(config: ProjectConfig) -> int:
    count = config.get(PROJECT_ONBOARDING_SEEN_COUNT)
    return count if isinstance(count, int) else 0


def should_show_project_onboarding(cwd: str | Path) -> bool:
    """
    `shouldShowProjectOnboarding`: not once completed, seen `MAX_PROJECT_ONBOARDING_SEEN_COUNT`
    times, or under `IS_DEMO`; otherwise while the onboarding is incomplete.
    """
    config = get_current_project_config(cwd)
    if (config.get(HAS_COMPLETED_PROJECT_ONBOARDING) or _seen_count(config) >= MAX_PROJECT_ONBOARDING_SEEN_COUNT
            or os.environ.get("IS_DEMO")):
        return False
    return not is_project_onboarding_complete(cwd)


def increment_project_onboarding_seen_count(cwd: str | Path) -> None:
    """`incrementProjectOnboardingSeenCount`."""
    save_current_project_config(
        cwd, lambda current: {**current, PROJECT_ONBOARDING_SEEN_COUNT: _seen_count(current) + 1})


def create_project_onboarding_feed(steps: list[Step], cwd: str | Path, home: str | Path | None = None) -> str:
    """
    `createProjectOnboardingFeed`: the title, then the enabled steps (incomplete ones first, a tick
    on a complete one), then the home-directory note when the session runs in `home`.
    """
    enabled = sorted((s for s in steps if s.is_enabled), key=lambda s: s.is_complete)
    lines = [f"{TICK} {s.text}" if s.is_complete else s.text for s in enabled]
    if Path(cwd) == Path(home if home is not None else Path.home()):
        lines.append(HOME_DIRECTORY_WARNING)
    return "\n".join([PROJECT_ONBOARDING_FEED_TITLE, *lines])


__all__ = [
    "HAS_COMPLETED_PROJECT_ONBOARDING",
    "HOME_DIRECTORY_WARNING",
    "MAX_PROJECT_ONBOARDING_SEEN_COUNT",
    "PROJECT_ONBOARDING_FEED_TITLE",
    "PROJECT_ONBOARDING_SEEN_COUNT",
    "Step",
    "create_project_onboarding_feed",
    "get_steps",
    "increment_project_onboarding_seen_count",
    "is_project_onboarding_complete",
    "maybe_mark_project_onboarding_complete",
    "should_show_project_onboarding",
]
