"""
A scripted hook dispatcher for tests that exercise a hook *call site*, not the hooks.

`ScriptedDispatcher` is a real `HookDispatcher` (so `dispatcher_of` accepts it and every
per-event entry point runs its real code) whose `execute_hooks` replays what a test scripted
per event instead of spawning anything, and records each hook input it was handed.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping, Sequence

from app.loop.hooks.config import HooksConfigSnapshot
from app.loop.hooks.execute import HookDispatcher, HookOutsideReplResult
from app.loop.hooks.output import AggregatedHookResult


class ScriptedDispatcher(HookDispatcher):
    def __init__(
        self,
        log: list[str] | None = None,
        script: Mapping[str, Sequence[AggregatedHookResult]] | None = None,
        *,
        outside: Mapping[str, Sequence[HookOutsideReplResult]] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        super().__init__(snapshot=HooksConfigSnapshot(), session_id="s", cwd=".", project_dir=".")
        self.log = log if log is not None else []
        self.script = dict(script or {})
        self.outside = dict(outside or {})
        self.raises = raises
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def has_hook_for_event(self, event: str) -> bool:
        return True

    async def execute_hooks(
        self, hook_input: Mapping[str, Any], tool_use_id: str, **kwargs: Any
    ) -> AsyncIterator[AggregatedHookResult]:
        event = str(hook_input["hook_event_name"])
        self.log.append(event)
        self.seen.append((event, dict(hook_input)))
        if self.raises is not None:
            raise self.raises
        for item in self.script.get(event, ()):
            yield item

    async def execute_hooks_outside_repl(
        self, hook_input: Mapping[str, Any], **kwargs: Any
    ) -> list[HookOutsideReplResult]:
        event = str(hook_input["hook_event_name"])
        self.log.append(event)
        self.seen.append((event, dict(hook_input)))
        return list(self.outside.get(event, ()))

    def payload(self, event: str) -> dict[str, Any]:
        return next(p for e, p in self.seen if e == event)
