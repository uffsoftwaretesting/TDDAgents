import pytest

from services.markdown_converter import convert, MarkdownConversionError


def test_convert_header():
    markdown_text = "# Hello World"
    html = convert(markdown_text)
    # Expect an <h1> element with the correct content
    assert "<h1>Hello World</h1>" in html


def test_convert_emphasis():
    markdown_text = "*italic text*"
    html = convert(markdown_text)
    # The <em> tag should wrap the italic text
    assert "<em>italic text</em>" in html


def test_conversion_exception(monkeypatch):
    # Simulate failure in the underlying markdown library
    def fake_markdown(text):
        raise RuntimeError("Conversion failed")

    # Patch markdown.markdown inside our service module
    monkeypatch.setattr(
        "services.markdown_converter.markdown.markdown",
        fake_markdown,
    )

    with pytest.raises(MarkdownConversionError):
        convert("any input")
