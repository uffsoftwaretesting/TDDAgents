"""
The REPL's status line while the agents work.

Ported from `reference/claude-code/src/components/Spinner.tsx` -> `SpinnerWithVerb` (the message: the
override, else the in-progress todo's `activeForm`, else a verb picked once from `getSpinnerVerbs()`,
followed by `…`) and `components/Spinner/SpinnerAnimationRow.tsx` (the status after it: the elapsed time
and `↓ N tokens` once the turn has run `SHOW_TOKENS_AFTER_MS`, or always when verbose, joined by ` · ` in
parentheses; the tokens only when there are some).

*Divergences* (user decision 2026-11-14, from a recorded session: each refresh of the line redraws the
prompt): the line is static and refreshed once a second, so the glyph does not animate (upstream's
`useAnimationFrame(50)` would redraw the prompt twenty times a second); the override is the running
tool's activity (`getActivityDescription`, "Running npm install"), because a call's row prints only
when its result arrives; and the status ends with `esc to interrupt`, which upstream shows in the prompt's
footer. The thinking indicator, teammates and the width gating are not ported.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence

from app.utils.format import format_duration, format_number

#: The static glyph (`SpinnerGlyph` at rest): TDDAgents' shield, `⛨` (user decision 2026-11-17; upstream's
#: frames are `·✢*✶✻✽`, `components/Spinner/utils.ts`).
STATUS_GLYPH = "⛨"
#: `SHOW_TOKENS_AFTER_MS`.
SHOW_TOKENS_AFTER_MS = 30_000
#: `figures.arrowDown`.
ARROW_DOWN = "↓"
#: The interrupt hint (upstream's prompt footer).
INTERRUPT_HINT = "esc to interrupt"


def pick_spinner_verb(verbs: Sequence[str], choice: Callable[[Sequence[str]], str] = random.choice) -> str:
    """`sample(getSpinnerVerbs()) ?? 'Working'`."""
    return choice(verbs) if verbs else "Working"


def spinner_message(override: str | None, active_form: str | None, verb: str) -> str:
    """`overrideMessage ?? currentTodo?.activeForm ?? randomVerb`, then `…`."""
    return f"{override or active_form or verb}…"


def spinner_status_line(message: str, elapsed_ms: float, tokens: int, *, verbose: bool = False) -> str:
    """`⛨ <message> (<elapsed> · ↓ <tokens> tokens · esc to interrupt)`."""
    parts: list[str] = []
    if verbose or elapsed_ms > SHOW_TOKENS_AFTER_MS:
        parts.append(format_duration(elapsed_ms))
        if tokens > 0:
            parts.append(f"{ARROW_DOWN} {format_number(tokens)} tokens")
    parts.append(INTERRUPT_HINT)
    return f"{STATUS_GLYPH} {message} ({' · '.join(parts)})"


__all__ = [
    "ARROW_DOWN",
    "INTERRUPT_HINT",
    "SHOW_TOKENS_AFTER_MS",
    "STATUS_GLYPH",
    "pick_spinner_verb",
    "spinner_message",
    "spinner_status_line",
]
