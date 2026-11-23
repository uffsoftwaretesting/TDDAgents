"""
The title box's feed column (`app/components/logo_v2/feed.py`, `logo_v2.py`), upstream's `LogoV2` layout
(`components/LogoV2/LogoV2.tsx`, `Feed.tsx`, `utils/logoV2Utils.ts`): beside the logo behind a divider from
`HORIZONTAL_MIN_COLUMNS` on, under it after a rule when narrower, always inside the box; `/help for commands`
after the box.
"""

from __future__ import annotations

from app.components.design_system.rich_render import plain_text
from app.components.logo_v2.feed import (
    FeedConfig,
    FeedLine,
    calculate_feed_width,
    feed_renderable,
)
from app.components.logo_v2.logo_v2 import (
    HELP_HINT,
    HORIZONTAL_MIN_COLUMNS,
    MIN_FEED_WIDTH,
    calculate_optimal_left_width,
    left_column_width,
    render_logo,
)

GREEN = "38;2;134;239;172"
FOLDER = "/home/u/.tddagents/workspaces/tdd-be4e5851317643db/project"
TIP = "Run /init to create a TDDAGENTS.md file with instructions for TDDAgents"
FEED = FeedConfig("Tips for getting started", (FeedLine(TIP),))


def logo(width: int, feed: FeedConfig | None = FEED, color: bool = False) -> str:
    return render_logo("claude-sonnet-5-5", FOLDER, width=width, color=color, home="/home/u", feed=feed)


def test_the_feed_config():
    assert FEED.plain() == f"Tips for getting started\n{TIP}"
    assert calculate_feed_width(FEED) == len(TIP)
    assert calculate_feed_width(FeedConfig("A long title here", (FeedLine("x"),))) == len("A long title here")
    text = feed_renderable(FEED)
    assert text.plain == FEED.plain()
    assert str(text.style) == ""  # nothing styles the whole feed: the lines are not bold
    assert [(span.start, span.end, str(span.style)) for span in text.spans] == [
        (0, len(FEED.title), "bold rgb(134,239,172)"), (len(FEED.title) + 1, len(FEED.plain()), "default")]


def test_a_wide_terminal_puts_the_feed_beside_the_logo_inside_the_box():
    lines = logo(120).split("\n")
    assert lines[0].startswith("╭─  TDDAgents v") and len(lines[0]) == 120
    title_row = next(line for line in lines if "⛨  TDDAgents" in line)
    assert "│ Tips for getting started" in title_row  # same row, behind the divider
    tip_row = next(line for line in lines if "Run /init" in line)
    assert "claude-sonnet-5-5 · Red → Green → Refactor" in tip_row
    assert "~/.tddagents/workspaces/tdd-be4e5851317643db/project" in logo(120)  # whole, not cut
    assert lines[-2].startswith("╰") and lines[-1] == f"  {HELP_HINT}"


def test_the_logo_column_gives_way_so_the_feed_keeps_its_minimum():
    model_lines = ["⛨  TDDAgents", "claude-sonnet-5-5 · Red → Green → Refactor", "~/" + "x" * 60]
    assert calculate_optimal_left_width(["ab"]) == 20 + 4  # the floor, plus the padding
    assert calculate_optimal_left_width(model_lines) == 62 + 4
    left = left_column_width(model_lines, 90)
    assert left == 90 - 6 - 3 - MIN_FEED_WIDTH
    assert left_column_width(model_lines, 200) == 66
    assert left_column_width(model_lines, HORIZONTAL_MIN_COLUMNS - 1) is None
    narrow_lines = logo(72).split("\n")
    assert any("│ Tips for getting started" in line for line in narrow_lines)
    assert all(len(line) <= 72 for line in narrow_lines)


def test_a_narrow_terminal_stacks_the_feed_under_the_logo_inside_the_box():
    lines = logo(60).split("\n")
    folder = next(i for i, line in enumerate(lines) if "~/.tddagents" in line)
    rule = next(i for i, line in enumerate(lines) if "────" in line and line.startswith("│"))
    title = next(i for i, line in enumerate(lines) if "Tips for getting started" in line)
    bottom = next(i for i, line in enumerate(lines) if line.startswith("╰"))
    assert folder < rule < title < bottom
    assert all(len(line) <= 60 for line in lines)


def test_without_a_feed_only_the_logo():
    text = logo(100, feed=None)
    assert "Tips" not in text and "⛨  TDDAgents" in text and text.endswith(f"  {HELP_HINT}")


def test_in_colour_the_feed_title_is_bold_green():
    ansi = logo(100, color=True)
    assert f"\x1b[1;{GREEN}mTips for getting started" in ansi
    assert plain_text(ansi) == logo(100)
