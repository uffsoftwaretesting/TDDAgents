"""
Extracting a bundled skill's reference files on first invocation.

Ported from `reference/claude-code/src/skills/bundledSkills.ts`: `getBundledSkillExtractDir`,
`extractBundledSkillFiles`, `writeSkillFiles`, `safeWriteFile`, `resolveSkillFilePath`, and the
extract-once memoization `registerBundledSkill` wraps `getPromptForCommand` in.

Upstream compiles a bundled skill's reference files into the CLI and writes them, on the skill's
first invocation, under the per-process `getBundledSkillsRoot()`. Its read check allows that
subtree, so the model reads the files without a prompt (`checkReadableInternalPath`). TDDAgents'
bundled skills are files under `app/prompts/skills/<name>/`. Their reference files (everything
beside `SKILL.md`, which is the prompt itself) are copied to the same kind of directory, with
upstream's protections: a random per-process nonce in the path, owner-only modes, and exclusive,
no-follow creation. A failed extraction leaves the skill working without its base directory.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from app.utils.permissions.filesystem import get_bundled_skills_root

logger = logging.getLogger(__name__)

#: The skill's prompt; it is sent as the skill's text, never extracted.
SKILL_FILE = "SKILL.md"

_SAFE_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
_extracted: dict[str, str | None] = {}
_lock = threading.Lock()


def get_bundled_skill_extract_dir(skill_name: str) -> str:
    """`getBundledSkillExtractDir`: deterministic within the process."""
    return os.path.join(get_bundled_skills_root(), skill_name)


def bundled_skill_files(source_dir: str) -> dict[str, str]:
    """The skill's reference files, by path relative to its directory: all but `SKILL.md`."""
    root = Path(source_dir)
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative != SKILL_FILE and "__pycache__" not in path.parts:
            files[relative] = path.read_text(encoding="utf-8")
    return files


def resolve_skill_file_path(base_dir: str, rel_path: str) -> str:
    """`resolveSkillFilePath`: a skill-relative path under `base_dir`; traversal raises."""
    normalized = os.path.normpath(rel_path)
    if os.path.isabs(normalized) or ".." in normalized.split(os.sep) or ".." in normalized.split("/"):
        raise ValueError(f"bundled skill file path escapes skill dir: {rel_path}")
    return os.path.join(base_dir, normalized)


def _safe_write_file(path: str, content: str) -> None:
    """`safeWriteFile`: create exclusively, never through a symlink, owner-only."""
    fd = os.open(path, _SAFE_WRITE_FLAGS, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def write_skill_files(directory: str, files: dict[str, str]) -> None:
    """`writeSkillFiles`: each parent made once, owner-only, then each file written safely."""
    targets = [(resolve_skill_file_path(directory, rel), content) for rel, content in files.items()]
    for parent in sorted({os.path.dirname(target) for target, _ in targets}):
        os.makedirs(parent, mode=0o700, exist_ok=True)
    for target, content in targets:
        _safe_write_file(target, content)


def extract_bundled_skill_files(skill_name: str, source_dir: str) -> str | None:
    """
    `extractBundledSkillFiles`, once per process (`registerBundledSkill`'s memoized promise): the
    directory written to, or `None` when the write failed and the skill goes on without it.
    """
    with _lock:
        if skill_name in _extracted:
            return _extracted[skill_name]
        directory = get_bundled_skill_extract_dir(skill_name)
        try:
            files = bundled_skill_files(source_dir)
            write_skill_files(directory, files)
            result: str | None = directory
        except (OSError, ValueError) as exc:
            logger.debug("Failed to extract bundled skill '%s' to %s: %s", skill_name, directory, exc)
            result = None
        _extracted[skill_name] = result
        return result


def clear_extracted_bundled_skills() -> None:
    """For tests: forget which skills were extracted in this process."""
    with _lock:
        _extracted.clear()


__all__ = [
    "bundled_skill_files",
    "clear_extracted_bundled_skills",
    "extract_bundled_skill_files",
    "get_bundled_skill_extract_dir",
    "resolve_skill_file_path",
    "write_skill_files",
]
