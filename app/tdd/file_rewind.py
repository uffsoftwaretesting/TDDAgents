"""
What rewinding the files does to the session's TDD state: the one place that decides it.

TDD-only (no upstream counterpart). Upstream's `print.ts` -> `handleRewindFiles` rewinds the
files and nothing else, because claude-code has no ledger. Here the ledger holds what `RunTests`
observed of files a rewind may have put back, so after every successful rewind
(`handle_rewind_files`, `app/utils/file_history.py`) **the ledger restarts in RED and the test
files are fingerprinted again as the item's baseline** (user decision 2026-11-01): the same
restart `update_tdd_plan` makes when the current item changes. It only erases evidence, never
creates it (`RunTests` stays the only observation writer). Items already done stay done.
"""

from __future__ import annotations

from dataclasses import replace

from app.state.app_state_store import AppState
from app.tdd.ledger import TddPhase


def tdd_state_after_file_rewind(state: AppState, baseline: str | None) -> AppState:
    """The app state once the files were rewound: the ledger back in RED over `baseline`."""
    return replace(state, phase_ledger=state.phase_ledger.transition_to(TddPhase.RED), last_test_run=None,
                   tdd_item_test_baseline=baseline)


__all__ = ["tdd_state_after_file_rewind"]
