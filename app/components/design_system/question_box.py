"""
A question the user answers, drawn in a box on a terminal that shows colour.

Ported in shape from `reference/claude-code/src/components/permissions/PermissionDialog.tsx` (a rounded
border in the dialog's colour, a bold title, the body), `PermissionPrompt.tsx` (the question, the options,
a dim "Esc to cancel" footer), `components/CustomSelect/select.tsx` / `design-system/ListItem.tsx` (each
option's number dim, its description dim beneath it, upstream's `compact-vertical` layout),
`AskUserQuestionPermissionRequest/QuestionNavigationBar.tsx` (the question's header as its title) and
`ExitPlanModePermissionRequest.tsx` ("Ready to code?", the plan as Markdown between dashed rules).

The REPL's questions are built as plain text (what a non-terminal and the tests read, and what
`question_reminder` parses); `style_question` reads that layout back and draws it:

* the heading: a first line starting `⚠ ` (a permission, sandbox or hook question) or `? ` (the model's
  question, `? [header] question (n/N)`) becomes the box's title;
* option lines (`  n. label — description`) after the last question line: the number dim, the label, the
  description dim on the next line;
* the footer (the last line when it says how to choose, `Choose …` / `Esc to cancel`) is written under the
  box, dim;
* the plan approval's plan (between `Here is the plan:` and `Would you like to proceed?`) is rendered as
  Markdown between dashed rules, under the title "Ready to code?";
* every other line is the body, as it is.

*Divergences* (user decision 2026-11-17): a full rounded box where upstream's `PermissionDialog` draws its
top border only, and TDDAgents' green (`theme.BRAND`) for every question where upstream uses `permission`
blue and `planMode` sage; every text in the terminal's own foreground (`theme.TEXT`, never upstream's dim
grey), the box full width (2026-11-17). Model text is never read as markup (`Text`, not `console.print` markup).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

from rich.box import ROUNDED
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.markdown import Heading, Markdown, MarkdownElement
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from app.components.design_system import theme
from app.components.design_system.rich_render import to_ansi

#: An option line: its number, then its label (and ` — description`).
OPTION_LINE = re.compile(r"^(\s*)(\d+)([.)])\s(.*)$")
#: The model's question heading: `? [header] question (n/N)`.
ASKED = re.compile(r"^\? \[(?P<header>.*?)\] (?P<question>.*?)(?P<count> \(\d+/\d+\))?$")
#: `ExitPlanModePermissionRequest`'s title.
PLAN_TITLE = "Ready to code?"
PLAN_START = "Here is the plan:"
PLAN_END = "Would you like to proceed?"
#: The separator between an option's label and its description in the plain layout.
DESCRIPTION_SEPARATOR = " — "


class _PlanHeading(Heading):
    """A plan's heading, left-aligned and bold in the brand colour (upstream's Markdown, not rich's centred box)."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        self.text.justify = "left"
        self.text.stylize(f"bold {theme.BRAND}")
        yield self.text


class PlanMarkdown(Markdown):
    """The plan's Markdown with `_PlanHeading` headings."""

    elements: ClassVar[dict[str, type[MarkdownElement]]] = {**Markdown.elements, "heading_open": _PlanHeading}


@dataclass
class _Parts:
    title: str | None = None
    body: list[RenderableType] = field(default_factory=list)
    footer: str | None = None


def _is_footer(line: str) -> bool:
    """The line that says how to answer: `Choose …`, or a reminder's retry ending `Esc to cancel.`."""
    return line.startswith("Choose") or line.rstrip().endswith("Esc to cancel.")


def _heading(first: str, parts: _Parts) -> bool:
    """The heading line as the box's title (and, for the model's question, its question line)."""
    asked = ASKED.match(first)
    if asked is not None:
        parts.title = f"? {asked['header']}{asked['count'] or ''}" if asked["header"] else f"?{asked['count'] or ''}"
        parts.body.append(Text(asked["question"], style=f"bold {theme.TEXT}"))
        return True
    if first.startswith("⚠ "):
        parts.title = first[2:]
        return True
    return False


def _option(match: re.Match[str]) -> Text:
    label, _, description = match[4].partition(DESCRIPTION_SEPARATOR)
    text = Text(f"{match[1]}{match[2]}{match[3]} ", style=f"bold {theme.TEXT}")
    text.append(label, style=f"bold {theme.TEXT}")
    if description:
        text.append("\n" + " " * (len(match[1]) + len(match[2]) + 2) + description, style=theme.TEXT)
    return text


def _plan(lines: list[str], start: int) -> tuple[RenderableType, int]:
    """The plan between `PLAN_START` and `PLAN_END`, as Markdown between dashed rules; the index after it."""
    end = next((i for i in range(start + 1, len(lines)) if lines[i] == PLAN_END), len(lines))
    markdown = "\n".join(lines[start + 1:end]).strip("\n")
    rule = Rule(characters="╌", style=theme.BRAND)
    return Group(Text(PLAN_START), rule, PlanMarkdown(markdown), rule), end


def style_question(plain: str, width: int) -> str:
    """`plain` (a question as the REPL builds it) in a box, as ANSI text `width` columns wide."""
    lines = plain.split("\n")
    parts = _Parts()
    if lines and lines[-1] and _is_footer(lines[-1]):
        parts.footer = lines.pop()
    start = 1 if lines and _heading(lines[0], parts) else 0
    asked_at = max((i for i, line in enumerate(lines) if line.rstrip().endswith("?")), default=-1)
    i = start
    while i < len(lines):
        line = lines[i]
        if line == PLAN_START:
            parts.title = PLAN_TITLE
            rendered, i = _plan(lines, i)
            parts.body.append(rendered)
            continue
        match = OPTION_LINE.match(line)
        if match is not None and i > asked_at:
            if not OPTION_LINE.match(lines[i - 1]) or i - 1 <= asked_at:
                parts.body.append(Text(""))  # a blank line before the options, as upstream's Select sits apart
            parts.body.append(_option(match))
        else:
            parts.body.append(Text(line, style=f"bold {theme.TEXT}" if i == asked_at else theme.TEXT))
        i += 1
    title = Text(f" {parts.title} ", style=f"bold {theme.BRAND}") if parts.title else None
    panel = Panel(Group(*parts.body), box=ROUNDED, border_style=theme.BRAND, title=title, title_align="left",
                  padding=(0, 1), width=width)
    footer = [Text(parts.footer, style=theme.TEXT)] if parts.footer else []
    return to_ansi(Group(panel, *footer), width)


__all__ = ["OPTION_LINE", "PLAN_TITLE", "style_question"]
