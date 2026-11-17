"""
How the end of a session looks on a terminal that shows colour (user decision 2026-11-17).

TDDAgents' own (upstream has no plan to finish nor a session report): drawn in the title's style
(`app/components/logo_v2/logo_v2.py`, TDDAgents' green rounded box, `app/components/design_system/theme.py`).

* `plan_done_box`: when the last sub-requirement is done, the plan's items, each `✔`, then how to go on;
* `session_report_box`: after `/exit`, one box with the plan's progress, the final run, the tokens, the
  project and reports folders and the thread id; its title, colour and mark follow the outcome
  (completed, development stopped, no approved plan).

The plain text written to a non-terminal (and headless) stays as it was: `turn_end_text` and
`app/main.py`'s `report` lines.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rich.box import ROUNDED
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.components.design_system import theme
from app.components.design_system.rich_render import to_ansi

#: The line under the finished plan (`turn_end_text`'s).
PLAN_DONE_HINT = "Add more with /plan add, or /exit."


def plan_done_box(items: Sequence[tuple[str, str]], width: int) -> str:
    """The finished plan: `(id, text)` per item, each ticked, then `PLAN_DONE_HINT`."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    grid.add_column(overflow="fold")  # a long item wraps under its own text, not under the tick
    for item_id, text in items:
        grid.add_row(Text.assemble(("✔ ", theme.SUCCESS), (f"{item_id}.", f"bold {theme.TEXT}")),
                     Text(text, style=theme.TEXT))
    lines: list[RenderableType] = [grid, Text(""), Text(PLAN_DONE_HINT, style=theme.TEXT)]
    title = Text(" ✔ All sub-requirements done ", style=f"bold {theme.BRAND}")
    return to_ansi(Panel(Group(*lines), box=ROUNDED, border_style=theme.BRAND, title=title, title_align="left",
                         padding=(0, 1), width=width), width)


@dataclass(frozen=True)
class SessionReport:
    """What the end-of-session box says."""

    #: `completed`, `stopped` or `unplanned`.
    outcome: str
    thread_id: str
    project: str
    reports: str
    done: int = 0
    total: int = 0
    #: The final run's one-line summary; `None` when there was none (no approved plan).
    final_run: str | None = None
    #: Total and cached tokens; `None` when unknown.
    tokens: tuple[int, int] | None = None
    #: The terminal the session stopped on, and its current item, when development stopped.
    stopped_on: str | None = None
    current: str | None = None
    #: The plan file of a session that ended in plan mode.
    plan_file: str | None = None


#: Each outcome's title and colour.
OUTCOMES: dict[str, tuple[str, str]] = {
    "completed": ("✅ TDDAgents · session complete", theme.BRAND),
    "stopped": ("⚠ TDDAgents · development stopped", theme.WARNING),
    "unplanned": ("📋 TDDAgents · no approved plan", theme.WARNING),
}


def session_report_box(report: SessionReport, width: int) -> str:
    """The end-of-session box (see the module docstring)."""
    title, colour = OUTCOMES[report.outcome]
    rows = Table.grid(padding=(0, 2))
    rows.add_column(style=f"bold {theme.TEXT}", no_wrap=True)
    rows.add_column(overflow="fold")
    if report.total:
        rows.add_row("Plan", f"{report.done}/{report.total} sub-requirements done")
    if report.stopped_on is not None:
        rows.add_row("Stopped", report.stopped_on + (f" · current: {report.current}" if report.current else ""))
    if report.final_run is not None:
        passed = report.final_run.startswith("passed")
        rows.add_row("Final run", Text(report.final_run, style=theme.SUCCESS if passed else theme.WARNING))
    if report.plan_file is not None:
        rows.add_row("Plan so far", report.plan_file)
    if report.tokens is not None:
        total, cached = report.tokens
        rows.add_row("Tokens", f"{total:,} (cached {cached:,})")
    rows.add_row("Project", report.project)
    rows.add_row("Reports", report.reports)
    rows.add_row("Thread", Text.assemble(report.thread_id, ("  (resume: --thread-id)", theme.TEXT)))
    panel = Panel(rows, box=ROUNDED, border_style=colour, title=Text(f" {title} ", style=f"bold {colour}"),
                  title_align="left", padding=(1, 2), width=width)
    return to_ansi(panel, width)


__all__ = ["OUTCOMES", "PLAN_DONE_HINT", "SessionReport", "plan_done_box", "session_report_box"]
