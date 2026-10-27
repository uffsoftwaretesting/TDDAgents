---
name: UpdatePlan
description: Changes the session's TDD plan (its sub-requirements) after the user approves the change.
---
Changes the session's TDD plan: the ordered sub-requirements that are implemented one at a time, each through its own Red -> Green cycle.

Use it when the user asks to add, reword, remove or reorder sub-requirements, or when the work shows the plan must change. Every call is shown to the user as a before/after diff and applies only if they approve it; a rejection comes back with their feedback.

Operations, applied in order:
- `add`: a new sub-requirement (`text`), at `position` (1-based) or at the end.
- `edit`: new `text` for the item `id`. Editing a sub-requirement that is already done reopens it: it needs a new Red -> Green cycle.
- `remove`: drop the item `id`.
- `move`: put the item `id` at `position`.

No operation marks a sub-requirement done. An item is done only when RunTests has observed its failing test and then the passing one; the system then moves to the next item by itself.
