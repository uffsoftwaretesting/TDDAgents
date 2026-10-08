"""
Read-before-write bookkeeping for the file tools.

Ported from `reference/claude-code/src/utils/fileStateCache.ts` -> `FileState`, the value
type of `ToolUseContext.readFileState`. `ReadFile` records what it saw; `Edit` and
`WriteFile` refuse to touch an existing file that was never read, or that changed on disk
since it was read, so the model cannot overwrite content it has not seen.

Two deliberate divergences:

* **No timestamp.** The `Workspace` protocol exposes no mtime, so staleness is decided by
  comparing content — the fallback upstream itself uses for full reads. `content` always
  holds the full file as it was on disk, even after a ranged read, so the comparison is
  exact for partial views too.
* **No LRU bound.** Upstream caps the cache by entry count and bytes; this repository
  carries no such ceilings, and a run's working set is one workspace.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FileState:
    """What a tool last observed of one file, keyed by normalized workspace path."""

    content: str
    offset: int | None = None
    limit: int | None = None
    is_partial_view: bool = False


__all__ = ["FileState"]
