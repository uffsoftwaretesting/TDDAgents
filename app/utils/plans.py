"""
The session's plan file (plan mode).

Ported from `reference/claude-code/src/utils/plans.ts` -> `getPlanSlug` (`MAX_SLUG_RETRIES`),
`getPlansDirectory`, `getPlanFilePath`, `getPlan`, and `utils/permissions/filesystem.ts` ->
`isSessionPlanFile`.

Upstream keeps the slug in a per-session cache in global state and records it in its transcript
entries, which `copyPlanForResume` reads back (`getSlugFromLog`). Here one `SessionPlans` object
per session carries the directory and the slug on the `ToolContext` (`session_plans`), and the
slug is saved beside the session's TDD plan sidecar, so a resumed session reuses its plan file.

*Divergences:* the slug lives in a sidecar file, not in transcript entries; the remote-session
plan recovery (`file_snapshot`, `recoverPlanFromMessages`) and the fork copy have no equivalent
here.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.utils.words import generate_word_slug

logger = logging.getLogger(__name__)

#: `MAX_SLUG_RETRIES`: how many slugs are tried before a taken one is kept.
MAX_SLUG_RETRIES = 10

#: The sidecar holding the session's slug, beside `tdd-plan.json` (`copyPlanForResume`).
PLAN_SLUG_FILE_NAME = "plan-slug"


def get_plans_directory(settings: Mapping[str, Any], project_root: str | Path, config_home: str | Path) -> Path:
    """
    `getPlansDirectory`: the `plansDirectory` setting, resolved against the project root, when it
    stays inside it; else `<config home>/plans`. The directory is created.
    """
    configured = settings.get("plansDirectory")
    default = Path(config_home) / "plans"
    path = default
    if isinstance(configured, str) and configured:
        root = os.path.normpath(os.path.abspath(project_root))
        resolved = os.path.normpath(os.path.join(root, configured))
        if resolved == root or resolved.startswith(root + os.sep):
            path = Path(resolved)
        else:
            logger.error("plansDirectory must be within project root: %s", configured)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("Could not create the plans directory %s", path)
    return path


def new_plan_slug(directory: Path, generate: Callable[[], str] = generate_word_slug) -> str:
    """`getPlanSlug`'s generation: a slug whose `{slug}.md` is free, else the last one tried."""
    slug = ""
    for _ in range(MAX_SLUG_RETRIES):
        slug = generate()
        if not (directory / f"{slug}.md").exists():
            break
    return slug


@dataclass(frozen=True, slots=True)
class SessionPlans:
    """The session's plans directory and slug: `getPlanFilePath`, `getPlan`, `isSessionPlanFile`."""

    directory: Path
    slug: str

    def file_path(self, agent_id: str | None = None) -> Path:
        """`getPlanFilePath`: `{slug}.md`, or `{slug}-agent-{agentId}.md` for a subagent."""
        name = f"{self.slug}.md" if not agent_id else f"{self.slug}-agent-{agent_id}.md"
        return self.directory / name

    def read(self, agent_id: str | None = None) -> str | None:
        """`getPlan`: the plan text, or `None` when there is no plan file yet."""
        try:
            return self.file_path(agent_id).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def is_session_plan_file(self, absolute_path: str) -> bool:
        """`isSessionPlanFile`: the main or an agent plan file of this session (normalized first)."""
        normalized = os.path.normpath(absolute_path)
        return normalized.startswith(str(self.directory / self.slug)) and normalized.endswith(".md")


def load_session_plans(
    session_dir: str | Path,
    settings: Mapping[str, Any],
    project_root: str | Path,
    config_home: str | Path,
    generate: Callable[[], str] = generate_word_slug,
) -> SessionPlans:
    """
    The session's `SessionPlans`: the slug saved in `session_dir` when the session has one
    (`copyPlanForResume`), else a new one, saved there.
    """
    directory = get_plans_directory(settings, project_root, config_home)
    sidecar = Path(session_dir) / PLAN_SLUG_FILE_NAME
    try:
        slug = sidecar.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        slug = ""
    if not slug:
        slug = new_plan_slug(directory, generate)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(slug, encoding="utf-8")
    return SessionPlans(directory=directory, slug=slug)


def session_plan_target(context: Any, raw_path: str) -> str | None:
    """
    The normalized path of `raw_path` when it names this session's plan file (main or agent),
    else `None`. As upstream, the file tools reach it on the host: only an absolute path counts,
    since the plan-mode instructions name the file absolutely.
    """
    plans = getattr(context, "session_plans", None)
    expanded = os.path.expanduser(str(raw_path or ""))
    if plans is None or not os.path.isabs(expanded):
        return None
    normalized = os.path.normpath(expanded)
    return normalized if plans.is_session_plan_file(normalized) else None


__all__ = [
    "MAX_SLUG_RETRIES",
    "PLAN_SLUG_FILE_NAME",
    "SessionPlans",
    "get_plans_directory",
    "load_session_plans",
    "new_plan_slug",
    "session_plan_target",
]
