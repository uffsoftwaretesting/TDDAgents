import pytest
from pydantic import ValidationError

from app.config.settings import get_settings
from app.schemas.markdown import MarkdownInput, HTMLResponse, ErrorResponse


def test_markdown_input_valid_length():
    max_len = get_settings().markdown_max_length
    content = "a" * max_len
    mi = MarkdownInput(content=content)
    assert mi.content == content


def test_markdown_input_empty_content_raises():
    with pytest.raises(ValidationError):
        MarkdownInput(content="")


def test_markdown_input_too_long_content_raises():
    max_len = get_settings().markdown_max_length
    too_long = "a" * (max_len + 1)
    with pytest.raises(ValidationError):
        MarkdownInput(content=too_long)


def test_markdown_input_wrong_type_raises():
    with pytest.raises(ValidationError):
        MarkdownInput(content=123)


def test_html_response_success_and_data():
    html = "<p>Hello</p>"
    resp = HTMLResponse(data={"html": html})
    assert resp.success is True
    # pydantic converte o dicionário em objeto com atributo html
    assert hasattr(resp.data, "html")
    assert resp.data.html == html


def test_html_response_missing_data_raises():
    with pytest.raises(ValidationError):
        HTMLResponse()


def test_error_response_defaults_and_fields():
    code = 400
    message = "Bad Request"
    err = ErrorResponse(error={"code": code, "message": message})
    assert err.success is False
    assert hasattr(err.error, "code")
    assert hasattr(err.error, "message")
    assert err.error.code == code
    assert err.error.message == message


def test_error_response_missing_error_raises():
    with pytest.raises(ValidationError):
        ErrorResponse()