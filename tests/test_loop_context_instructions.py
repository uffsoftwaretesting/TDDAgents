"""
Tests for Part E3: Instruction-file loading (app/utils/frontmatter_parser.py).
"""


from app.utils.frontmatter_parser import strip_html_comments


def test_strip_html_comments_simple() -> None:
    text = "Hello <!-- comment -->World"
    assert strip_html_comments(text) == "Hello World"
    assert strip_html_comments("") == ""


def test_strip_html_comments_multiline() -> None:
    text = "Start\n<!--\nmultiline\ncomment\n-->\nEnd"
    assert strip_html_comments(text) == "Start\n\nEnd"


def test_strip_html_comments_nested_or_iterative() -> None:
    # Iterative stripping until stable
    text = "Before <!-- outer <!-- inner --> outer --> After"
    cleaned = strip_html_comments(text)
    assert "<!--" not in cleaned
    assert "-->" not in cleaned
