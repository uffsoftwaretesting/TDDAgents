"""
The title the REPL opens with.

Ported in shape from `reference/claude-code/src/components/LogoV2/LogoV2.tsx` (a rounded box in the
"claude" colour whose border title is ` Claude Code v{version} `, with the feed column at its right behind a
divider, l.415-436) and `CondensedLogo.tsx` (the name, the version, `model · billing` and the working
directory): here a rounded `rich` panel in TDDAgents' green (`app/components/design_system/theme.py`, user
decision 2026-11-17) titled ` TDDAgents v{VERSION} `, with the shield, the model and TDD's cycle, and the
project folder (user decision 2026-11-17). A feed (the project onboarding tips) sits at the right of that,
behind a vertical divider, when the terminal is `HORIZONTAL_MIN_COLUMNS` wide (`utils/logoV2Utils.ts`
`getLayoutMode`, `calculateOptimalLeftWidth`), and under it, after a rule, when it is narrower (user
decision 2026-11-23: upstream's compact layout drops the feed). It is rendered once, to a string with its
ANSI styles, and written above the input line like any other text.

*Divergences:* no mascot and no "Welcome back" greeting; the panel spans the terminal, and `HELP_HINT`
follows it.
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.box import MINIMAL, ROUNDED
from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from app.components.design_system import theme
from app.components.design_system.rich_render import plain_text, to_ansi
from app.components.logo_v2.feed import FeedConfig, feed_renderable
from app.components.spinner.spinner import STATUS_GLYPH
from app.constants.product import VERSION

#: The product's name, as the title shows it.
PRODUCT_NAME = "TDDAgents"
#: The colour of the logo box and title: TDDAgents' green (upstream's `theme.claude` is orange).
BRAND_COLOR = theme.BRAND
#: The width the panel takes when the caller gives none.
DEFAULT_WIDTH = 80
#: The line under the title that points at the commands.
HELP_HINT = "/help for commands"
#: `getLayoutMode`: the feed sits beside the logo from this many columns on.
HORIZONTAL_MIN_COLUMNS = 70
#: `calculateOptimalLeftWidth`'s padding around the widest logo line.
LEFT_PADDING = 4
#: `calculateLayoutDimensions`: the feed column is never narrower than this (`Math.max(30, …)`).
MIN_FEED_WIDTH = 30
#: `calculateOptimalLeftWidth`'s floor (upstream's "minimum for clawd art").
MIN_LEFT_WIDTH = 20
#: The panel's border and horizontal padding (`padding=(1, 2)`), and the columns' cell padding and divider.
PANEL_CHROME = 2 + 4
COLUMNS_CHROME = 1 + 1 + 1


def display_folder(folder: str | Path, home: str | Path | None = None) -> str:
    """The folder with the home directory written `~`, as upstream's logo shows the cwd."""
    path = str(folder)
    home_dir = str(home if home is not None else Path.home())
    if path == home_dir:
        return "~"
    return "~" + path[len(home_dir):] if path.startswith(home_dir + os.sep) else path


def calculate_optimal_left_width(lines: list[str]) -> int:
    """
    `calculateOptimalLeftWidth`: the widest logo line plus its padding, at least `MIN_LEFT_WIDTH`. Upstream
    also caps it at `MAX_LEFT_WIDTH` (50) and truncates the folder; here the room the feed leaves is the only
    cap (`left_column_width`), so a session's workspace path, about 50 columns, stays whole.
    """
    return max(MIN_LEFT_WIDTH, *(cell_len(line) for line in lines)) + LEFT_PADDING


def left_column_width(lines: list[str], width: int) -> int | None:
    """
    The logo column's width beside a feed, `width` columns wide in all; `None` when the two do not fit
    (`getLayoutMode`'s `compact`). Upstream's `calculateLayoutDimensions` caps the feed column instead and
    lets it shrink below `MIN_FEED_WIDTH`; here the logo column gives way, so the tips stay readable.
    """
    if width < HORIZONTAL_MIN_COLUMNS:
        return None
    room = width - PANEL_CHROME - COLUMNS_CHROME - MIN_FEED_WIDTH
    left = min(calculate_optimal_left_width(lines), room)
    return left if left >= MIN_LEFT_WIDTH else None


def render_logo(model: str, folder: str | Path, *, width: int = DEFAULT_WIDTH, color: bool = True,
                home: str | Path | None = None, feed: FeedConfig | None = None) -> str:
    """
    The title panel, `width` columns wide, then `HELP_HINT`; with `feed`, the feed beside the logo (or
    under it on a narrow terminal). With `color` its ANSI styles, else plain text.
    """
    lines = [f"{STATUS_GLYPH}  {PRODUCT_NAME}", f"{model} · Red → Green → Refactor", display_folder(folder, home)]
    logo = Text()
    logo.append(f"{lines[0]}\n", style=f"bold {BRAND_COLOR}")
    logo.append(f"{lines[1]}\n", style=theme.TEXT)
    logo.append(lines[2], style=theme.TEXT)
    # A long folder or model wraps rather than being cut with an ellipsis.
    logo.overflow = "fold"
    body: RenderableType = logo
    left = left_column_width(lines, width) if feed is not None else None
    if feed is not None and left is not None:
        # `LogoV2`'s horizontal layout: the logo column, a divider in the box's colour, the feed column.
        columns = Table(box=MINIMAL, show_header=False, show_edge=False, border_style=BRAND_COLOR,
                        padding=(0, 1), pad_edge=False, expand=True)
        columns.add_column(width=left)
        columns.add_column(ratio=1)
        columns.add_row(logo, feed_renderable(feed))
        body = columns
    elif feed is not None:
        body = Group(logo, Rule(style=BRAND_COLOR), feed_renderable(feed))
    panel = Panel(body, box=ROUNDED, title=f" {PRODUCT_NAME} v{VERSION} ", title_align="left",
                  border_style=BRAND_COLOR, padding=(1, 2), width=width)
    styled = to_ansi(Group(panel, Text(f"  {HELP_HINT}", style=theme.TEXT)), width)
    return styled if color else plain_text(styled)


__all__ = ["BRAND_COLOR", "DEFAULT_WIDTH", "HELP_HINT", "HORIZONTAL_MIN_COLUMNS", "MIN_FEED_WIDTH",
           "PRODUCT_NAME", "calculate_optimal_left_width", "display_folder", "left_column_width", "render_logo"]
