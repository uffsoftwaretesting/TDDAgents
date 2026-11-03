"""
The session's working directory: where the shell is now, and where the session started.

Ported from `reference/claude-code/src/bootstrap/state.ts` -> `getOriginalCwd`, `getCwdState`,
`setCwd`, `setInlinePlugins`, `getInlinePlugins`, `setSessionTrustAccepted`,
`getSessionTrustAccepted`, `setFlagSettingsInline`, `getFlagSettingsInline`, and
(`SessionBootstrap`) `getSessionId`,
`getIsNonInteractiveSession`. Upstream keeps
these in the process-wide `STATE`, one session per process. A TDDAgents process can hold
several sessions (the test suite, the experiment drivers), each over its own workspace, so the
working directory is kept per workspace object: the original directory is the workspace's root
(`permission_cwd`), and the tracked one starts there. A workspace that goes away takes its entry
with it (weak keys). The session's identity is a `SessionBootstrap` value on the run's `AppState`.

The `--plugin-dir` directories (`inlinePlugins`) stay process-wide, as upstream: the flag is a
property of the process invocation, set once by `app/main.py` before any plugin load. So does
`sessionTrustAccepted`: the interactive entry point sets it once the trust dialog is past
(`app/session/driver.py`, upstream's `showSetupScreens`). So do the SDK host's inline flag
settings (`flagSettingsInline`, `apply_flag_settings`): `-p` runs one session per process, and the
settings files are read process-wide (`app/utils/settings/settings.py` merges them into
`flagSettings`).
"""

from __future__ import annotations

import os
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_CWD: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
#: Workspaces that cannot be weakly referenced (a `SimpleNamespace` stand-in), by identity.
_CWD_BY_ID: dict[int, str] = {}
#: `STATE.inlinePlugins`: the `--plugin-dir` directories, in flag order.
_INLINE_PLUGINS: list[str] = []
#: `STATE.sessionTrustAccepted`: trust given for this process only (the home directory's case).
_SESSION_TRUST_ACCEPTED: list[bool] = [False]
#: `STATE.flagSettingsInline`: the settings an SDK host applied (`apply_flag_settings`), or `None`.
_FLAG_SETTINGS_INLINE: list[dict[str, Any] | None] = [None]


def set_flag_settings_inline(settings: Mapping[str, Any] | None) -> None:
    """`setFlagSettingsInline`."""
    _FLAG_SETTINGS_INLINE[0] = dict(settings) if settings is not None else None


def get_flag_settings_inline() -> dict[str, Any] | None:
    """`getFlagSettingsInline`."""
    value = _FLAG_SETTINGS_INLINE[0]
    return dict(value) if value is not None else None


def set_session_trust_accepted(accepted: bool) -> None:
    """`setSessionTrustAccepted`."""
    _SESSION_TRUST_ACCEPTED[0] = accepted


def get_session_trust_accepted() -> bool:
    """`getSessionTrustAccepted`."""
    return _SESSION_TRUST_ACCEPTED[0]


def set_inline_plugins(plugins: list[str]) -> None:
    """`setInlinePlugins`."""
    _INLINE_PLUGINS[:] = plugins


def get_inline_plugins() -> list[str]:
    """`getInlinePlugins`."""
    return list(_INLINE_PLUGINS)


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


@dataclass(frozen=True, slots=True)
class SessionBootstrap:
    """
    The run's slice of upstream's process-wide `STATE` that file history reads
    (`getSessionId`, `getOriginalCwd`, `getIsNonInteractiveSession`). Upstream holds one session
    per process; here it is a value on the run's `AppState` (`AppState.bootstrap`), set once
    when the run is opened, so two sessions in one interpreter never share it.

    The defaults are upstream's initial `STATE`: `isInteractive: false`, and no original
    directory (`get_original_cwd` then falls back to the process directory).
    """

    #: `STATE.sessionId`: the run id, the transcript's file name.
    session_id: str = ""
    #: `STATE.originalCwd`: the folder the session was opened in (the workspace root here).
    original_cwd: str | None = None
    #: `STATE.isInteractive`: the REPL (`True`) or a headless / SDK run (`False`).
    is_interactive: bool = False

    def get_original_cwd(self) -> str:
        """`getOriginalCwd`."""
        return self.original_cwd if self.original_cwd is not None else os.getcwd()

    def get_is_non_interactive_session(self) -> bool:
        """`getIsNonInteractiveSession`: `!STATE.isInteractive`."""
        return not self.is_interactive
