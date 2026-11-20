"""
OSC sequences, and their passthrough for a terminal multiplexer.

Ported from `reference/claude-code/src/ink/termio/osc.ts` -> `OSC_PREFIX`, `ST`, `osc`, `OSC` (the
notification codes), `wrapForMultiplexer`, and from `ink/termio/ansi.ts` -> `ESC`, `BEL`, `SEP`. Only what the
notifier writes is ported.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: `ansi.ts`.
ESC = "\x1b"
BEL = "\x07"
SEP = ";"
#: `OSC_PREFIX`: ESC ].
OSC_PREFIX = ESC + "]"
#: `ST`: ESC \\, the String Terminator.
ST = ESC + "\\"

#: `OSC`'s notification codes.
OSC_ITERM2 = 9
OSC_KITTY = 99
OSC_GHOSTTY = 777


def osc(*parts: str | int, terminal: str | None = None) -> str:
    """`osc`: ESC ] p1;p2;… then ST for Kitty (it beeps on BEL), BEL for the others."""
    terminator = ST if terminal == "kitty" else BEL
    return f"{OSC_PREFIX}{SEP.join(str(p) for p in parts)}{terminator}"


def wrap_for_multiplexer(sequence: str, environ: Mapping[str, str] | None = None) -> str:
    """
    `wrapForMultiplexer`: tmux and GNU screen intercept escape sequences, so a DCS passthrough tunnels them
    to the outer terminal (tmux 3.3+ needs `allow-passthrough`; without it tmux drops the DCS silently).
    A raw BEL is never wrapped: tmux's own bell-action must see it.
    """
    env = os.environ if environ is None else environ
    if env.get("TMUX"):
        escaped = sequence.replace(ESC, ESC + ESC)
        return f"{ESC}Ptmux;{escaped}{ESC}\\"
    if env.get("STY"):
        return f"{ESC}P{sequence}{ESC}\\"
    return sequence


__all__ = ["BEL", "ESC", "OSC_GHOSTTY", "OSC_ITERM2", "OSC_KITTY", "OSC_PREFIX", "SEP", "ST", "osc",
           "wrap_for_multiplexer"]
