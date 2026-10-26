"""
Tests for Part E4: Markdown prompt loader (app/loop/prompts/loader.py).
"""

import pytest

from app.loop.prompts.loader import (
    parse_markdown_frontmatter,
    render_prompt,
)


def test_render_prompt_basic() -> None:
    template = "Hello {{NAME}}, welcome to {{PROJECT}}!"
    rendered = render_prompt(template, {"NAME": "Alice", "PROJECT": "TDDAgents"})
    assert rendered == "Hello Alice, welcome to TDDAgents!"


def test_render_prompt_none_or_empty_vars() -> None:
    template = "Hello {{NAME}}!"
    assert render_prompt(template, None) == "Hello {{NAME}}!"
    assert render_prompt(template, {}) == "Hello {{NAME}}!"


def test_render_prompt_strip_comments_false() -> None:
    template = "Hello <!-- note --> world"
    assert render_prompt(template, {}, strip_comments=False) == "Hello <!-- note --> world"
    assert render_prompt(template, {}, strip_comments=True) == "Hello  world"
    # Default is strip_comments=True
    assert render_prompt(template) == "Hello  world"


def test_acceptance_test_unresolved_placeholder_survives_visibly() -> None:
    """
    CRITICAL ACCEPTANCE TEST (§4.1):
    An unresolved placeholder must survive visibly into the rendered text as {{VAR}},
    never render as empty.
    """
    template = "Fix the requirement: {{sub_requsite}} in file {{TARGET_FILE}}."
    rendered = render_prompt(template, {"TARGET_FILE": "main.py"})
    assert "{{sub_requsite}}" in rendered
    assert "Fix the requirement: {{sub_requsite}} in file main.py." == rendered


def test_parse_markdown_frontmatter_valid() -> None:
    content = """---
name: refactorer
description: Improves structure without changing behaviour.
phase: refactor
tools: [ReadFile, WriteFile, RunTests]
permissionMode: workspace_write
memory: run
model: inherit
---
# Refactorer Instructions
Improve the codebase after green.
"""
    fm, body = parse_markdown_frontmatter(content)
    assert fm["name"] == "refactorer"
    assert fm["description"] == "Improves structure without changing behaviour."
    assert fm["phase"] == "refactor"
    assert fm["tools"] == ["ReadFile", "WriteFile", "RunTests"]
    assert fm["permissionMode"] == "workspace_write"
    assert fm["memory"] == "run"
    assert fm["model"] == "inherit"
    assert "Improve the codebase after green." in body


def test_parse_markdown_frontmatter_leading_whitespace_and_multiple_dashes() -> None:
    content_ws = "  \n---\nname: spaced\n---\nBody"
    fm, body = parse_markdown_frontmatter(content_ws)
    assert fm["name"] == "spaced"
    assert body == "Body"

    content_multiple_dashes = "---\nname: foo\n---\nPart 1\n---\nPart 2\n---\nPart 3"
    fm2, body2 = parse_markdown_frontmatter(content_multiple_dashes)
    assert fm2["name"] == "foo"
    assert body2 == "Part 1\n---\nPart 2\n---\nPart 3"


def test_parse_markdown_frontmatter_no_frontmatter() -> None:
    content = "Just plain markdown body without dashes."
    fm, body = parse_markdown_frontmatter(content)
    assert fm == {}
    assert body == "Just plain markdown body without dashes."


def test_parse_markdown_frontmatter_invalid_yaml() -> None:
    content = "---\n: broken yaml :\n---\nBody content"
    fm, body = parse_markdown_frontmatter(content)
    assert fm == {}
    assert body == "Body content"


def test_parse_markdown_frontmatter_edge_cases(caplog: pytest.LogCaptureFixture) -> None:
    # Malformed YAML
    fm1, b1 = parse_markdown_frontmatter("---\n: malformed: yaml: :\n---\nBody 1")
    assert fm1 == {}
    assert b1 == "Body 1"
    assert "Failed to parse YAML frontmatter:" in caplog.text

    # YAML is a list, not a dict
    fm2, b2 = parse_markdown_frontmatter("---\n- item 1\n- item 2\n---\nBody 2")
    assert fm2 == {}
    assert b2 == "Body 2"

    # No frontmatter
    fm3, b3 = parse_markdown_frontmatter("Just markdown text without frontmatter")
    assert fm3 == {}
    assert b3 == "Just markdown text without frontmatter"
