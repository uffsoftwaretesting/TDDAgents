"""
The session's working directory: where the shell is now, and where the session started.

Ported from `reference/claude-code/src/bootstrap/state.ts` -> `getOriginalCwd`, `getCwdState`,
`setCwd`. Upstream keeps these in the process-wide `STATE`, one session per process. A
TDDAgents process can hold several sessions (the test suite, the experiment drivers), each over
its own workspace, so the state is kept per workspace object: the original directory is the
workspace's root (`permission_cwd`), and the tracked one starts there. A workspace that goes
away takes its entry with it (weak keys).
"""

from __future__ import annotations

import os
import weakref
from typing import Any

_CWD: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
#: Workspaces that cannot be weakly referenced (a `SimpleNamespace` stand-in), by identity.
_CWD_BY_ID: dict[int, str] = {}


def get_original_cwd(workspace: Any) -> str:
    """`getOriginalCwd`: the workspace root, else the process directory."""
    root = getattr(workspace, "root", None)
    return str(root) if root is not None else os.getcwd()


def get_cwd_state(workspace: Any) -> str:
    """`getCwdState`: the tracked directory, the original one until a command moves it."""
    if workspace is None:
        return os.getcwd()
    try:
        tracked = _CWD.get(workspace)
    except TypeError:
        tracked = _CWD_BY_ID.get(id(workspace))
    return tracked or get_original_cwd(workspace)


def set_cwd(workspace: Any, path: str) -> None:
    """`setCwd` (the state half): record where the shell now is."""
    if workspace is None:
        return
    try:
        _CWD[workspace] = path
    except TypeError:
        _CWD_BY_ID[id(workspace)] = path
