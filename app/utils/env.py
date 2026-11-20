"""
The terminal TDDAgents runs in.

Ported from `reference/claude-code/src/utils/env.ts` -> `detectTerminal` (the `env.terminal` the notifier and
`osc` read). The IDE detections (Cursor, Windsurf, JetBrains bundle ids, Visual Studio) are kept in their
upstream order; the macOS-only and ant-only branches are not ported.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


def detect_terminal(environ: Mapping[str, str] | None = None) -> str | None:
    """`detectTerminal`: the terminal's name from the environment, `None` when nothing says."""
    env = os.environ if environ is None else environ
    if env.get("CURSOR_TRACE_ID"):
        return "cursor"
    askpass = env.get("VSCODE_GIT_ASKPASS_MAIN", "")
    for name in ("cursor", "windsurf", "antigravity"):
        if name in askpass:
            return name
    if env.get("VisualStudioVersion"):
        return "visualstudio"
    if env.get("TERMINAL_EMULATOR") == "JetBrains-JediTerm":
        return "pycharm"
    term = env.get("TERM", "")
    if term == "xterm-ghostty":
        return "ghostty"
    if "kitty" in term:
        return "kitty"
    if env.get("TERM_PROGRAM"):
        return env["TERM_PROGRAM"]
    for variable, name in (("TMUX", "tmux"), ("STY", "screen"), ("KONSOLE_VERSION", "konsole"),
                           ("GNOME_TERMINAL_SERVICE", "gnome-terminal"), ("XTERM_VERSION", "xterm"),
                           ("VTE_VERSION", "vte-based"), ("TERMINATOR_UUID", "terminator"),
                           ("KITTY_WINDOW_ID", "kitty"), ("ALACRITTY_LOG", "alacritty"), ("TILIX_ID", "tilix"),
                           ("WT_SESSION", "windows-terminal")):
        if env.get(variable):
            return name
    if env.get("SESSIONNAME") and term == "cygwin":
        return "cygwin"
    if env.get("MSYSTEM"):
        return env["MSYSTEM"].lower()
    return None


__all__ = ["detect_terminal"]
