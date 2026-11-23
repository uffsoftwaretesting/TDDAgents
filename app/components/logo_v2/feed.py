"""
A column of the title box: a titled list of short lines.

Ported from `reference/claude-code/src/components/LogoV2/Feed.tsx` -> `FeedLine`, `FeedConfig`,
`calculateFeedWidth` and `Feed` (the title bold in the brand colour, then one line each). Upstream's
timestamps, footer, empty message and custom content are not ported: TDDAgents' one feed, the project
onboarding tips (`app/project_onboarding_state.py`), uses none of them.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len
from rich.text import Text

from app.components.design_system import theme


@dataclass(frozen=True)
class FeedLine:
    """`FeedLine`."""

    text: str


@dataclass(frozen=True)
class FeedConfig:
    """`FeedConfig`: the feed's title and its lines."""

    title: str
    lines: tuple[FeedLine, ...]

    def plain(self) -> str:
        """The feed as text: the title, then each line."""
        return "\n".join([self.title, *(line.text for line in self.lines)])


def calculate_feed_width(config: FeedConfig) -> int:
    """`calculateFeedWidth`: the widest of the title and the lines, in terminal cells."""
    return max([cell_len(config.title), *(cell_len(line.text) for line in config.lines)])


def feed_renderable(config: FeedConfig) -> Text:
    """`Feed`: the title bold in the brand colour, then each line in the terminal's own colour."""
    text = Text()
    text.append(config.title, style=f"bold {theme.BRAND}")
    for line in config.lines:
        text.append("\n")
        text.append(line.text, style=theme.TEXT)
    return text


__all__ = ["FeedConfig", "FeedLine", "calculate_feed_width", "feed_renderable"]
