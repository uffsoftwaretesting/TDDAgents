"""
How long a waiting question stays silent before the notifier speaks.

Ported from `reference/claude-code/src/hooks/useNotifyAfterTimeout.ts` -> `DEFAULT_INTERACTION_THRESHOLD_MS`.
The hook itself is the REPL's `_notify_after_timeout` (`app/screens/repl.py`): the line-based REPL has no
render cycle, so the interaction clock is stamped when a question is shown and on every line typed.
"""

#: `DEFAULT_INTERACTION_THRESHOLD_MS`: 6 s without input after a question is shown.
DEFAULT_INTERACTION_THRESHOLD_MS = 6000

__all__ = ["DEFAULT_INTERACTION_THRESHOLD_MS"]
