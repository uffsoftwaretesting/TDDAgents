"""
What rewinding the files does to the session's TDD state: the one place that decides it.

TDD-only (no upstream counterpart). Upstream's `print.ts` -> `handleRewindFiles` rewinds the
files and nothing else, because claude-code has no ledger. Here the ledger holds what `RunTests`
observed of files a rewind may have put back, and the plan marks items done by those
observations. `handle_rewind_files` (`app/utils/file_history.py`) applies this function to the
app state after every successful rewind, so the decision is this function's body alone.

**Undecided (an open question for the user): the rewind leaves the TDD state as it is**, which
is upstream's behaviour. Whatever is decided must keep the ledger's invariant: a change may move
the phase or reset evidence, never create it (`RunTests` is the only observation writer).
"""

from __future__ import annotations

from app.state.app_state_store import AppState


def tdd_state_after_file_rewind(state: AppState) -> AppState:
    """The app state once the files were rewound: unchanged, as upstream (pending decision)."""
    return state


__all__ = ["tdd_state_after_file_rewind"]
