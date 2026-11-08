"""
The in-memory tail of sandbox violations.

Ported from `@anthropic-ai/sandbox-runtime` 0.0.44 `src/sandbox/sandbox-violation-store.ts` ->
`SandboxViolationStore`, and `macos-sandbox-utils.ts` -> `SandboxViolationEvent` (the type only).
Only macOS's log monitor adds violations upstream; on Linux the store stays empty, so
`annotateStderrWithSandboxFailures` changes nothing there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.utils.sandbox.runtime.sandbox_utils import encode_sandboxed_command

#: `maxSize`: how many violations the tail keeps.
MAX_VIOLATIONS = 100


@dataclass(frozen=True)
class SandboxViolationEvent:
    """`SandboxViolationEvent`."""

    line: str
    timestamp: datetime
    command: str | None = None
    encoded_command: str | None = None


ViolationListener = Callable[[list[SandboxViolationEvent]], None]


class SandboxViolationStore:
    """`SandboxViolationStore`: the last `MAX_VIOLATIONS` violations, the running total, and listeners."""

    def __init__(self) -> None:
        self._violations: list[SandboxViolationEvent] = []
        self._total_count = 0
        self._listeners: list[ViolationListener] = []

    def add_violation(self, violation: SandboxViolationEvent) -> None:
        self._violations.append(violation)
        self._total_count += 1
        if len(self._violations) > MAX_VIOLATIONS:
            self._violations = self._violations[-MAX_VIOLATIONS:]
        self._notify_listeners()

    def get_violations(self, limit: int | None = None) -> list[SandboxViolationEvent]:
        if limit is None:
            return list(self._violations)
        return self._violations[-limit:] if limit > 0 else []

    def get_count(self) -> int:
        return len(self._violations)

    def get_total_count(self) -> int:
        return self._total_count

    def get_violations_for_command(self, command: str) -> list[SandboxViolationEvent]:
        encoded = encode_sandboxed_command(command)
        return [v for v in self._violations if v.encoded_command == encoded]

    def clear(self) -> None:
        """Empties the tail; the running total is kept."""
        self._violations = []
        self._notify_listeners()

    def subscribe(self, listener: ViolationListener) -> Callable[[], None]:
        """Adds `listener`, calls it at once with the current tail, and returns its removal."""
        self._listeners.append(listener)
        listener(self.get_violations())

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _notify_listeners(self) -> None:
        violations = self.get_violations()
        for listener in list(self._listeners):
            listener(violations)


__all__ = ["MAX_VIOLATIONS", "SandboxViolationEvent", "SandboxViolationStore", "ViolationListener"]
