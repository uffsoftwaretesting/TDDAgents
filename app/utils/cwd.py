"""
The current working directory a tool resolves against.

Ported from `reference/claude-code/src/utils/cwd.ts` -> `pwd`, `getCwd`. Not ported:
`runWithCwdOverride`, the per-agent override for worktree agents, which TDDAgents has no
counterpart for.
"""

from __future__ import annotations

from typing import Any

from app.bootstrap.state import get_cwd_state, get_original_cwd


def get_cwd(context: Any) -> str:
    """`getCwd` / `pwd`: the shell's tracked directory for this context's workspace."""
    return get_cwd_state(getattr(context, "workspace", None))


def get_original_cwd_for(context: Any) -> str:
    """`getOriginalCwd` for this context's workspace."""
    return get_original_cwd(getattr(context, "workspace", None))
