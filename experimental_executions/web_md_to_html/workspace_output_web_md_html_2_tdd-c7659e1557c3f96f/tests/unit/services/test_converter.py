import pytest
from app.services.converter import convert_markdown_to_html
from app.core.errors import ConversionError


def test_convert_empty_markdown_returns_empty_string():
    # Markdown vazio deve retornar HTML vazio
    result = convert_markdown_to_html("")
    assert result == ""


def test_convert_heading():
    md = "# Test Heading"
    html = convert_markdown_to_html(md)
    assert "<h1>Test Heading</h1>" in html


def test_convert_unordered_list():
    md = "- item1\n- item2"
    html = convert_markdown_to_html(md)
    assert "<ul>" in html
    assert "<li>item1</li>" in html
    assert "<li>item2</li>" in html


def test_convert_link():
    md = "[example](http://example.com)"
    html = convert_markdown_to_html(md)
    assert '<a href="http://example.com">example</a>' in html


def test_convert_image():
    md = "![alt text](http://example.com/image.png)"
    html = convert_markdown_to_html(md)
    # Expect an <img> tag with src and alt attributes
    assert '<img' in html
    assert 'src="http://example.com/image.png"' in html
    assert 'alt="alt text"' in html


def test_convert_raises_conversion_error_on_internal_exception(monkeypatch):
    # Simulate internal exception in markdown conversion
    def fake_markdown(_):
        raise RuntimeError("conversion failed")

    # Patch the markdown function inside our converter
    import app.services.converter as conv_mod
    monkeypatch.setattr(conv_mod, 'markdown', fake_markdown)

    with pytest.raises(ConversionError) as excinfo:
        convert_markdown_to_html("any text")
    # The ConversionError should wrap the original error
    assert "conversion failed" in str(excinfo.value)
