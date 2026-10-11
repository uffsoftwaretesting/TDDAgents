"""
Persisting oversized tool results, and the preview that replaces them (Phase 2).

Ported from:
- `reference/claude-code/src/utils/toolResultStorage.ts` -> `getPersistenceThreshold`,
  `persistToolResult`, `generatePreview`, `buildLargeToolResultMessage`,
  `maybePersistLargeToolResult`
- `reference/claude-code/src/constants/toolLimits.ts` -> `DEFAULT_MAX_RESULT_SIZE_CHARS`
- `reference/claude-code/src/utils/format.ts` -> `formatFileSize`

The shape is upstream's, including the parts that look like details and are not:

* **The preview is head-only.** 2000 bytes, cut at the last newline *only* if that newline
  is past the halfway mark, otherwise a hard cut. No tail.
* **`DEFAULT_MAX_RESULT_SIZE_CHARS` is a clamp, not a default.** A tool declaring more than
  50 000 still persists at 50 000; the declared number only bites when it is lower.
* **`math.inf` is a hard opt-out**, checked before anything else. `ReadFile` is the one
  tool that uses it, for upstream's reason: persisting a read creates a
  ReadFile -> file -> ReadFile loop, and the tool already bounds itself.
* **Persistence failure is fail-open.** If the file cannot be written the original result
  is returned untouched — a storage problem must not also destroy the output.
* **Writes are create-only.** Upstream opens with `'wx'` and swallows `EEXIST`, because a
  tool_use id is unique and its content deterministic, so a replay must not rewrite.

The directory layout mirrors upstream's — `<config>/projects/<sanitized cwd>/<run>/tool-results/`
— rather than living inside the workspace, so generated code never contains the agent's own
scratch. Upstream's `Read` is not workspace-bounded and can open that path directly; ours is,
so `app/loop/tools/fs.py` admits this one directory as an extra read root, which is what
keeps the path in the preview a path the model can actually use.
"""

from __future__ import annotations

import logging
import math
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: `toolLimits.ts` -> `DEFAULT_MAX_RESULT_SIZE_CHARS`. A ceiling on every tool's own figure.
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000

#: `toolLimits.ts`: `MAX_TOOL_RESULT_TOKENS * BYTES_PER_TOKEN`, the fallback threshold.
MAX_TOOL_RESULT_TOKENS = 100_000
BYTES_PER_TOKEN = 4
MAX_TOOL_RESULT_BYTES = MAX_TOOL_RESULT_TOKENS * BYTES_PER_TOKEN

#: `toolLimits.ts` -> `MAX_TOOL_RESULTS_PER_MESSAGE_CHARS`.
MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200_000

#: `toolResultStorage.ts` -> `PREVIEW_SIZE_BYTES`.
PREVIEW_SIZE_BYTES = 2000

#: `toolResultStorage.ts` -> the tags that wrap the replacement message.
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"

#: `toolResultStorage.ts` -> `TOOL_RESULTS_SUBDIR`.
TOOL_RESULTS_SUBDIR = "tool-results"

#: `sessionStoragePortable.ts` -> `MAX_SANITIZED_LENGTH`.
MAX_SANITIZED_LENGTH = 200

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")
_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


def config_home() -> Path:
    """`envUtils.ts` -> `getClaudeConfigHomeDir`, under this project's own name."""
    override = os.environ.get("TDDAGENTS_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".tddagents"


def sanitize_path(name: str) -> str:
    """`sessionStoragePortable.ts` -> `sanitizePath`, hash suffix included."""
    sanitized = _NON_ALNUM.sub("-", name)
    if len(sanitized) > MAX_SANITIZED_LENGTH:
        import hashlib

        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        sanitized = f"{sanitized[:MAX_SANITIZED_LENGTH]}-{digest}"
    return sanitized


def safe_file_stem(tool_use_id: str) -> str:
    """
    The filename for a tool_use id.

    Upstream writes `${id}.txt` with the id verbatim, because its ids come from the API and
    are already alphanumeric. Ours arrive from whichever provider answered, so anything that
    could leave the directory is replaced — for a well-formed id such as `toolu_01ABC` this
    is the identity, which is what keeps the layout upstream's.
    """
    cleaned = _UNSAFE_IN_FILENAME.sub("-", tool_use_id).lstrip(".")
    return cleaned[:MAX_SANITIZED_LENGTH] or "result"


def tool_results_dir(run_id: str, working_directory: str | Path) -> Path:
    """`<config>/projects/<sanitized cwd>/<run>/tool-results/`."""
    return (
        config_home()
        / "projects"
        / sanitize_path(str(working_directory))
        / sanitize_path(run_id)
        / TOOL_RESULTS_SUBDIR
    )


def get_persistence_threshold(declared_max_result_size_chars: float) -> float:
    """
    `getPersistenceThreshold`, minus the feature-flag override that has no analogue here.

    The infinity opt-out is checked first, as upstream checks it before the override so no
    remote configuration can switch persistence back on for a tool that must never have it.
    """
    if not math.isfinite(declared_max_result_size_chars):
        return declared_max_result_size_chars
    return min(declared_max_result_size_chars, DEFAULT_MAX_RESULT_SIZE_CHARS)


def format_file_size(size_bytes: int) -> str:
    """`format.ts` -> `formatFileSize`: `N bytes`, then KB/MB/GB at one decimal, `.0` stripped."""
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    for unit in ("KB", "MB", "GB"):
        size = size_bytes / 1024
        size_bytes = int(size_bytes / 1024)
        if size < 1024 or unit == "GB":
            rendered = f"{size:.1f}".removesuffix(".0")
            return f"{rendered}{unit}"
    return f"{size_bytes} bytes"  # pragma: no cover - the loop always returns


def generate_preview(content: str, max_bytes: int = PREVIEW_SIZE_BYTES) -> tuple[str, bool]:
    """
    `generatePreview` -> `(preview, has_more)`.

    The newline snap applies only past the halfway mark, so a long unbroken line is cut
    hard rather than collapsing the preview to almost nothing.
    """
    if len(content) <= max_bytes:
        return content, False
    head = content[:max_bytes]
    last_newline = head.rfind("\n")
    cut = last_newline if last_newline > max_bytes * 0.5 else max_bytes
    return content[:cut], True


def build_large_tool_result_message(
    original_size: int, filepath: Path | str, preview: str, has_more: bool
) -> str:
    """`buildLargeToolResultMessage`, wording and layout verbatim."""
    message = f"{PERSISTED_OUTPUT_TAG}\n"
    message += (
        f"Output too large ({format_file_size(original_size)}). "
        f"Full output saved to: {filepath}\n\n"
    )
    message += f"Preview (first {format_file_size(PREVIEW_SIZE_BYTES)}):\n"
    message += preview
    message += "\n...\n" if has_more else "\n"
    message += PERSISTED_OUTPUT_CLOSING_TAG
    return message


def persist_tool_result(
    content: str, tool_use_id: str, run_id: str, working_directory: str | Path
) -> Path | None:
    """
    Write the full result where the model can fetch it. `None` if it could not be written.

    Create-only, as upstream: an existing file for this tool_use id already holds exactly
    this content, so a replay reuses it instead of rewriting it.
    """
    directory = tool_results_dir(run_id, working_directory)
    filepath = directory / f"{safe_file_stem(tool_use_id)}.txt"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create the tool-result store for %s: %s", tool_use_id, exc)
        return None

    try:
        # Create-only. An existing file for this id already holds exactly this content, so a
        # replay reuses it; `x` says that in one flag instead of a stat that could race.
        with open(filepath, "x", encoding="utf-8") as handle:
            handle.write(content)
    except FileExistsError:
        return filepath
    except OSError as exc:
        logger.warning("Could not persist tool result %s: %s", tool_use_id, exc)
        return None
    return filepath


def maybe_persist_large_result(
    content: str,
    *,
    tool_name: str,
    tool_use_id: str,
    run_id: str,
    working_directory: str | Path,
    max_result_size_chars: float = MAX_TOOL_RESULT_BYTES,
) -> str:
    """
    `maybePersistLargeToolResult`: the content to send the model, in upstream's order.

    Empty output is replaced first — upstream's inc-4586, where an empty tool result makes
    some models emit a stop sequence — then the threshold decides, then a write failure
    leaves the original content alone.
    """
    if not content.strip():
        return f"({tool_name} completed with no output)"

    threshold = get_persistence_threshold(max_result_size_chars)
    size = len(content)
    if size <= threshold:
        return content

    filepath = persist_tool_result(content, tool_use_id, run_id, working_directory)
    if filepath is None:
        return content

    preview, has_more = generate_preview(content)
    return build_large_tool_result_message(size, filepath, preview, has_more)


def _unused(*_args: Any) -> None:  # pragma: no cover - keeps `Any` imported for typing users
    return None


__all__ = [
    "BYTES_PER_TOKEN",
    "DEFAULT_MAX_RESULT_SIZE_CHARS",
    "MAX_TOOL_RESULTS_PER_MESSAGE_CHARS",
    "MAX_TOOL_RESULT_BYTES",
    "MAX_TOOL_RESULT_TOKENS",
    "PERSISTED_OUTPUT_CLOSING_TAG",
    "PERSISTED_OUTPUT_TAG",
    "PREVIEW_SIZE_BYTES",
    "TOOL_RESULTS_SUBDIR",
    "build_large_tool_result_message",
    "config_home",
    "format_file_size",
    "generate_preview",
    "get_persistence_threshold",
    "maybe_persist_large_result",
    "persist_tool_result",
    "safe_file_stem",
    "sanitize_path",
    "tool_results_dir",
]
