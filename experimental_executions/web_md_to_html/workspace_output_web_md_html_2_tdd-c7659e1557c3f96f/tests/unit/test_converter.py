import pytest
from app.services.converter import convert_markdown_to_html


def test_convert_empty_markdown():
    # Markdown vazio deve retornar HTML vazio
    assert convert_markdown_to_html("") == ""


def test_convert_simple_heading():
    md = "# Heading"
    html = convert_markdown_to_html(md)
    assert "<h1>Heading</h1>" in html


def test_convert_unordered_list():
    md = "- item1\n- item2"
    html = convert_markdown_to_html(md)
    assert "<ul>" in html
    assert "<li>item1</li>" in html
    assert "<li>item2</li>" in html


def test_convert_complex_elements():
    md = "**bold** and [link](http://example.com)"
    html = convert_markdown_to_html(md)
    # Verifica tag <strong> e <a href>
    assert "<strong>bold</strong>" in html
    assert '<a href="http://example.com">link</a>' in html
