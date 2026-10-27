"""
A background task's output file (Phase 8).

Ported from `reference/claude-code/src/utils/task/diskOutput.ts` -> `getTaskOutputDir`,
`getTaskOutputPath`, `initTaskOutputAsSymlink`, `getTaskOutput`, and
`utils/task/outputFormatting.ts` -> `getMaxTaskOutputLength`, `formatTaskOutput`.

A background agent's output file is a symlink to its sidechain transcript, so reading it shows
the worker's progress as it happens. *Divergence:* upstream puts the directory in the
project's temp dir (`<tmp>/claude-<uid>/<cwd>/<session>/tasks/`) because its reads are
auto-allowed there; here it is the session directory beside the transcript
(`<config>/projects/<cwd>/<run id>/tasks/`), which is this build's readable internal path for
the same reason (`checkReadPermissionForTool`).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from app.utils.session_storage_portable import get_project_dir

logger = logging.getLogger(__name__)

#: `DEFAULT_MAX_READ_BYTES`.
DEFAULT_MAX_READ_BYTES = 8 * 1024 * 1024
#: `TASK_MAX_OUTPUT_UPPER_LIMIT` / `TASK_MAX_OUTPUT_DEFAULT` (`outputFormatting.ts`).
TASK_MAX_OUTPUT_UPPER_LIMIT = 160_000
TASK_MAX_OUTPUT_DEFAULT = 32_000


def get_task_output_dir(context: Any) -> Path:
    """`getTaskOutputDir`: the run's session directory's `tasks/`."""
    store = getattr(context, "transcript_store", None)
    if store is not None:
        return Path(store.path).parent / str(store.session_id) / "tasks"
    root = getattr(getattr(context, "workspace", None), "root", None) or os.getcwd()
    return get_project_dir(str(root)) / str(context.run_id) / "tasks"


def get_task_output_path(context: Any, task_id: str) -> Path:
    """`getTaskOutputPath`."""
    return get_task_output_dir(context) / f"{task_id}.output"


def init_task_output_as_symlink(output_path: Path, target_path: Path) -> Path:
    """`initTaskOutputAsSymlink`: link (or relink) to the target; an empty file if linking fails."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.is_symlink() or output_path.exists():
            output_path.unlink()
        os.symlink(target_path, output_path)
    except OSError as exc:
        logger.warning("Could not link task output %s: %s", output_path, exc)
        output_path.write_text("", encoding="utf-8")
    return output_path


def get_task_output(output_path: Path, max_bytes: int = DEFAULT_MAX_READ_BYTES) -> str:
    """`getTaskOutput`: the file's tail, with a note for what was left out; `""` when missing."""
    try:
        data = output_path.read_bytes()
    except OSError:
        return ""
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace")
    omitted = len(data) - max_bytes
    tail = data[-max_bytes:].decode("utf-8", errors="replace")
    return f"[{round(omitted / 1024)}KB of earlier output omitted]\n{tail}"


def get_max_task_output_length() -> int:
    """`getMaxTaskOutputLength`: `TASK_MAX_OUTPUT_LENGTH`, bounded (`validateBoundedIntEnvVar`)."""
    raw = os.environ.get("TASK_MAX_OUTPUT_LENGTH")
    if not raw:
        return TASK_MAX_OUTPUT_DEFAULT
    try:
        parsed = int(raw)
    except ValueError:
        return TASK_MAX_OUTPUT_DEFAULT
    if parsed <= 0:
        return TASK_MAX_OUTPUT_DEFAULT
    return min(parsed, TASK_MAX_OUTPUT_UPPER_LIMIT)


def format_task_output(output: str, output_file: str) -> tuple[str, bool]:
    """`formatTaskOutput`: the output, or its tail behind a header naming the full file."""
    max_len = get_max_task_output_length()
    if len(output) <= max_len:
        return output, False
    header = f"[Truncated. Full output: {output_file}]\n\n"
    return header + output[-(max_len - len(header)):], True


__all__ = [
    "DEFAULT_MAX_READ_BYTES",
    "TASK_MAX_OUTPUT_DEFAULT",
    "TASK_MAX_OUTPUT_UPPER_LIMIT",
    "format_task_output",
    "get_max_task_output_length",
    "get_task_output",
    "get_task_output_dir",
    "get_task_output_path",
    "init_task_output_as_symlink",
]
