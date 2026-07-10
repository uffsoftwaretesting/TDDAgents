import pytest
import markdown2

from app.services.markdown_converter import convert_markdown_to_html, MarkdownConversionError


def test_convert_simple_markdown():
    # Given a simple markdown header
    md = "# Hello World"
    # When converting to HTML
    html = convert_markdown_to_html(md)
    # Then we receive a HTML string containing the expected <h1>
    assert isinstance(html, str)
    assert '<h1>Hello World</h1>' in html


def test_convert_non_string_input_raises_type_error():
    # Passing a non-string should immediately raise a TypeError
    with pytest.raises(TypeError):
        convert_markdown_to_html(123)


def test_markdown2_exception_should_raise_markdown_conversion_error(monkeypatch):
    # Simulate markdown2.markdown raising a runtime error
    def fake_markdown(text):
        raise RuntimeError("Library failure")
    monkeypatch.setattr(markdown2, 'markdown', fake_markdown)

    # Now calling conversion should raise our custom exception
    with pytest.raises(MarkdownConversionError) as excinfo:
        convert_markdown_to_html("Some *text*")
    # The exception message should indicate a conversion error
    assert "Error converting markdown" in str(excinfo.value)
