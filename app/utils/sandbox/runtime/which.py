"""
Finding an executable on `PATH`.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.44 `src/utils/which.ts` -> `whichSync`: the full path
of `bin`, or `None`. `shutil.which` stands in for `which` / `Bun.which`.
"""

from __future__ import annotations

import shutil


def which_sync(binary: str, path: str | None = None) -> str | None:
    """`whichSync`."""
    return shutil.which(binary, path=path)


__all__ = ["which_sync"]
