"""
The session's TDD plan: the sub-requirements the Planner produced, as state the user and the
model can change while the work goes on (Phase 11).

A sub-requirement is `pending` until its own Red -> Green -> Refactor cycle has been observed, then
`done`. The current item is the first one that is not done, so the order of the list *is* the
order of the work. Two rules carry the TDD guarantee and are enforced here, not by callers:

* **Nothing marks an item done except an observed cycle.** `complete_current` is the only
  transition to `done`, and it refuses unless the phase ledger shows the whole cycle. The
  plan tool, `/plan` and the planner can add, reword, remove or reorder items; none of them
  can declare one finished.
* **Changing a done item reopens it** (user decision 2026-10-27). Its new text needs a fresh
  cycle; the code written for the old text stays in the workspace.

The plan is persisted beside the session transcript, in `<session dir>/tdd-plan.json`, so a
resumed session knows which items are done. The ledger is never persisted: a resumed
session's current item starts in RED again.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.suite_fingerprint import fingerprint_test_files
from app.utils.session_storage_portable import get_project_dir

logger = logging.getLogger("TDDOrchestrator.Plan")

PLAN_FILE_NAME = "tdd-plan.json"


class PlanError(ValueError):
    """A plan operation that cannot apply (unknown item, empty text, an unobserved cycle)."""


class ItemStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class PlanItem:
    id: str
    text: str
    status: ItemStatus = ItemStatus.PENDING
    #: The ledger that completed it (`complete_current`): the observed cycle, for the events log.
    #: In memory only; the sidecar keeps id, text and status.
    completed_by: PhaseLedger | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class TddPlan:
    """An ordered, immutable list of sub-requirements; every operation returns a new plan."""

    items: tuple[PlanItem, ...] = ()

    @classmethod
    def from_texts(cls, texts: list[str] | tuple[str, ...]) -> TddPlan:
        plan = cls()
        for text in texts:
            plan = plan.add(text)
        return plan

    @property
    def current(self) -> PlanItem | None:
        """The first item that is not done: the one the work is on."""
        return next((item for item in self.items if item.status != ItemStatus.DONE), None)

    @property
    def is_complete(self) -> bool:
        """Every item has an observed cycle. An empty plan has nothing left to do."""
        return self.current is None

    @property
    def done_count(self) -> int:
        return sum(1 for item in self.items if item.status == ItemStatus.DONE)

    def get(self, item_id: str) -> PlanItem:
        for item in self.items:
            if item.id == item_id:
                return item
        raise PlanError(f"No sub-requirement with id {item_id!r}.")

    def _next_id(self) -> str:
        numbers = [int(item.id) for item in self.items if item.id.isdigit()]
        return str(max(numbers, default=0) + 1)

    def _index(self, item_id: str) -> int:
        return self.items.index(self.get(item_id))

    def add(self, text: str, position: int | None = None) -> TddPlan:
        """A new pending item, at `position` (1-based) or at the end."""
        item = PlanItem(id=self._next_id(), text=_clean(text))
        items = list(self.items)
        items.insert(len(items) if position is None else _slot(position, len(items) + 1), item)
        return replace(self, items=tuple(items))

    def edit(self, item_id: str, text: str) -> TddPlan:
        """New text for an item. A done item is reopened: the new text needs its own cycle."""
        index = self._index(item_id)
        items = list(self.items)
        items[index] = PlanItem(id=item_id, text=_clean(text), status=ItemStatus.PENDING)
        return replace(self, items=tuple(items))

    def remove(self, item_id: str) -> TddPlan:
        index = self._index(item_id)
        return replace(self, items=self.items[:index] + self.items[index + 1:])

    def move(self, item_id: str, position: int) -> TddPlan:
        """Move an item to `position` (1-based) in the list."""
        index = self._index(item_id)
        items = list(self.items)
        item = items.pop(index)
        items.insert(_slot(position, len(items) + 1), item)
        return replace(self, items=tuple(items))

    def replace_pending(self, texts: list[str] | tuple[str, ...]) -> TddPlan:
        """
        A re-scoped plan (`/requirements`): the done items stay done, in order, and every item
        not yet done is replaced by `texts`, with fresh ids.
        """
        plan = replace(self, items=tuple(item for item in self.items if item.status == ItemStatus.DONE))
        for text in texts:
            plan = plan.add(text)
        return plan

    def complete_current(self, ledger: PhaseLedger) -> TddPlan:
        """Mark the current item done; only an observed Red -> Green -> Refactor cycle allows it."""
        current = self.current
        if current is None:
            raise PlanError("Every sub-requirement is already done.")
        if not ledger.is_cycle_complete:
            raise PlanError(f"Sub-requirement {current.id} has no completed Red -> Green -> Refactor cycle.")
        index = self._index(current.id)
        items = list(self.items)
        items[index] = replace(current, status=ItemStatus.DONE, completed_by=ledger)
        return replace(self, items=tuple(items))

    def to_dict(self) -> dict[str, Any]:
        return {"items": [{"id": i.id, "text": i.text, "status": str(i.status)} for i in self.items]}

    @classmethod
    def from_dict(cls, data: Any) -> TddPlan:
        raw = data.get("items") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raise PlanError("A plan file holds an object with an 'items' list.")
        items = []
        for entry in raw:
            if not isinstance(entry, dict):
                raise PlanError("Each plan item is an object.")
            try:
                items.append(PlanItem(id=str(entry["id"]), text=_clean(str(entry["text"])),
                                      status=ItemStatus(entry.get("status", ItemStatus.PENDING))))
            except (KeyError, ValueError) as exc:
                raise PlanError(f"Invalid plan item {entry!r}: {exc}") from None
        return cls(items=tuple(items))


def _clean(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise PlanError("A sub-requirement needs text.")
    return stripped


def _slot(position: int, size: int) -> int:
    """A 1-based position, clamped to the list, as a 0-based insert index."""
    return min(max(position, 1), size) - 1


def render_plan(plan: TddPlan) -> str:
    """The plan as the user and the model see it: `✔` done, `▶` current, `·` still to do."""
    if not plan.items:
        return "(no sub-requirements)"
    current = plan.current
    lines = []
    for item in plan.items:
        mark = "✔" if item.status == ItemStatus.DONE else "▶" if item is current else "·"
        lines.append(f"{mark} {item.id}. {item.text}")
    return "\n".join(lines)


#: The plan-file section whose numbered items become the sub-requirements (plan mode, the Analyst).
IMPLEMENTATION_PLAN_HEADING = "Implementation Plan"

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_NUMBERED = re.compile(r"^(\s*)\d+[.)]\s+(.*\S)\s*$")


def parse_implementation_plan(plan_text: str) -> list[str]:
    """
    The sub-requirements of an approved plan file: the top-level numbered items of its
    `Implementation Plan` section (any heading level, any case), up to the next heading of the
    same or a higher level. Lines under an item (wrapped text, nested bullets) join its text; a
    deeper heading inside the section closes the open item without joining it.
    """
    lines = plan_text.splitlines()
    start, level = None, 0
    for index, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading and heading.group(2).strip().lower() == IMPLEMENTATION_PLAN_HEADING.lower():
            start, level = index + 1, len(heading.group(1))
            break
    if start is None:
        return []
    items: list[list[str]] = []
    indent: int | None = None
    item_open = False
    for line in lines[start:]:
        heading = _HEADING.match(line)
        if heading:
            if len(heading.group(1)) <= level:
                break
            item_open = False
            continue
        numbered = _NUMBERED.match(line)
        if numbered and (indent is None or len(numbered.group(1)) <= indent):
            indent = len(numbered.group(1))
            items.append([numbered.group(2)])
            item_open = True
        elif item_open and line.strip():
            items[-1].append(line.strip())
    return [" ".join(part for part in item if part) for item in items]


def plan_path(session_id: str, cwd: str | Path) -> Path:
    """`<project dir>/<session id>/tdd-plan.json`, beside the session's other files."""
    return get_project_dir(cwd) / session_id / PLAN_FILE_NAME


def save_plan(path: Path, plan: TddPlan) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_plan(path: Path) -> TddPlan | None:
    """The saved plan, or `None` when there is none. A corrupt file is logged and ignored."""
    try:
        return TddPlan.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, PlanError) as exc:
        logger.warning("Ignoring unreadable plan file %s: %s", path, exc)
        return None


def update_tdd_plan(context: Any, change: Callable[[TddPlan], TddPlan]) -> TddPlan:
    """
    Apply `change` to the session's plan in app state and save it (`ToolContext.tdd_plan_path`).

    The change is computed from the current plan first, so a refused operation (`PlanError`)
    leaves both the state and the file untouched. When the change moves the work to another
    sub-requirement — the current one done, removed, moved, reworded or replaced — the TDD
    ledger restarts in RED: what it observed belonged to the old item, and each item is done
    only by its own Red -> Green -> Refactor cycle. The new item's tests are fingerprinted as
    its baseline (Phase 12). Like phase-on-spawn, the restart can only erase evidence.
    """
    app_state = context.get_app_state()
    updated = change(app_state.tdd_plan)
    if updated.current != app_state.tdd_plan.current:
        ledger = app_state.phase_ledger
        reset_ledger = ledger.transition_to(TddPhase.RED)
        baseline = fingerprint_test_files(getattr(context, "workspace", None))
        context.set_app_state(lambda s: replace(s, tdd_plan=updated, phase_ledger=reset_ledger, last_test_run=None,
                                                tdd_item_test_baseline=baseline))
    else:
        context.set_app_state(lambda state: replace(state, tdd_plan=updated))
    if context.tdd_plan_path:
        save_plan(Path(context.tdd_plan_path), updated)
    return updated
