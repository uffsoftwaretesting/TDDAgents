"""
End-of-run export of the session workspace into `workspace_output_<thread_id>/`.

Code now lives in a real (local) workspace rather than the old `AgentState.file_system`
mirror, so the artifact is a copy of that tree. This also captures files created through
`Bash`, which the mirror lost. Tool scratch and caches never reach the artifact.
"""

from __future__ import annotations

import shutil
from pathlib import Path

#: Directory names never copied into the exported artifact.
EXPORT_EXCLUDES: tuple[str, ...] = (".tddagents", "__pycache__", ".pytest_cache", ".mypy_cache", ".git")


def export_run_workspace(source: Path | str, destination: Path | str) -> list[str]:
    """
    Copy `source` into `destination` (merging with anything already there) and return the
    exported files as sorted paths relative to `destination`. A missing source exports nothing.
    """
    src, dst = Path(source), Path(destination)
    if not src.is_dir():
        return []
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*EXPORT_EXCLUDES), dirs_exist_ok=True)
    exported = []
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if path.is_file() and not any(part in EXPORT_EXCLUDES for part in rel.parts):
            exported.append(rel.as_posix())
    return sorted(exported)


__all__ = ["EXPORT_EXCLUDES", "export_run_workspace"]
