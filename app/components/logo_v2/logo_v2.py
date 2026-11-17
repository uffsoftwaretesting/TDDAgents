"""
The title the REPL opens with.

Ported in shape from `reference/claude-code/src/components/LogoV2/LogoV2.tsx` (a rounded box in the
"claude" colour whose border title is ` Claude Code v{version} `) and `CondensedLogo.tsx` (the name, the
version, `model · billing` and the working directory): here a rounded `rich` panel in TDDAgents' green
(`app/components/design_system/theme.py`, user decision 2026-11-17) titled
` TDDAgents v{VERSION} `, with the shield, the model and TDD's cycle, and the project folder (user
decision 2026-11-17). It is rendered once, to a string with its ANSI styles, and written above the input
line like any other text.

*Divergences:* no mascot, no "Welcome back" greeting and no feed column (the onboarding tips print
after the title, as the REPL's other notices do); the panel is as wide as the terminal allows, up to
`MAX_WIDTH`.
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.box import ROUNDED
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from app.components.design_system import theme
from app.components.design_system.rich_render import plain_text, to_ansi

from app.components.spinner.spinner import STATUS_GLYPH
from app.constants.product import VERSION

#: The product's name, as the title shows it.
PRODUCT_NAME = "TDDAgents"
#: The colour of the logo box and title: TDDAgents' green (upstream's `theme.claude` is orange).
BRAND_COLOR = theme.BRAND
#: The widest the panel gets.
MAX_WIDTH = 72
#: The line under the title that points at the commands.
HELP_HINT = "/help for commands"


def display_folder(folder: str | Path, home: str | Path | None = None) -> str:
    """The folder with the home directory written `~`, as upstream's logo shows the cwd."""
    path = str(folder)
    home_dir = str(home if home is not None else Path.home())
    if path == home_dir:
        return "~"
    return "~" + path[len(home_dir):] if path.startswith(home_dir + os.sep) else path


def render_logo(model: str, folder: str | Path, *, width: int = MAX_WIDTH, color: bool = True,
                home: str | Path | None = None) -> str:
    """The title panel, then `HELP_HINT`; with `color` its ANSI styles, else plain text."""
    body = Text()
    body.append(f"{STATUS_GLYPH}  {PRODUCT_NAME}\n", style=f"bold {BRAND_COLOR}")
    body.append(f"{model} · Red → Green → Refactor\n", style=theme.INACTIVE)
    body.append(display_folder(folder, home), style=theme.INACTIVE)
    panel = Panel(body, box=ROUNDED, title=f" {PRODUCT_NAME} v{VERSION} ", title_align="left",
                  border_style=BRAND_COLOR, padding=(1, 2), width=width)
    styled = to_ansi(Group(panel, Text(HELP_HINT, style=theme.INACTIVE)), width)
    return styled if color else plain_text(styled)


__all__ = ["BRAND_COLOR", "HELP_HINT", "MAX_WIDTH", "PRODUCT_NAME", "display_folder", "render_logo"]
