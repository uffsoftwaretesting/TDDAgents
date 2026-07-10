import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config.settings import get_settings
from app.services.markdown_converter import MarkdownConversionError

client = TestClient(app)


def test_valid_markdown_conversion():
    payload = {"content": "# Hello Integration"}
    response = client.post("/convert/", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data.get("success") is True
    assert "data" in data
    html = data["data"].get("html")
    assert isinstance(html, str)
    assert "<h1>Hello Integration</h1>" in html


@pytest.mark.parametrize("body, expected_code", [
    (None, 422),  # corpo faltando
    ("not a json", 422),  # JSON malformado
])
def test_malformed_or_empty_json_body_returns_422(body, expected_code):
    headers = {"Content-Type": "application/json"}
    if body is None:
        response = client.post("/convert/", headers=headers)
    else:
        response = client.post("/convert/", data=body, headers=headers)
    assert response.status_code == expected_code
    data = response.json()
    assert data.get("success") is False
    assert "error" in data and isinstance(data["error"], dict)
    assert data["error"].get("code") == expected_code
    assert isinstance(data["error"].get("message"), str)


@pytest.mark.parametrize("payload", [
    {"content": ""},       # vazio
    {"content": "   "},    # somente whitespace
])
def test_empty_or_whitespace_content_returns_422(payload):
    response = client.post("/convert/", json=payload)
    assert response.status_code == 422
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == 422
    assert isinstance(data["error"]["message"], str)


def test_too_long_content_returns_422():
    max_len = get_settings().markdown_max_length
    content = "a" * (max_len + 1)
    response = client.post("/convert/", json={"content": content})
    assert response.status_code == 422
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == 422
    # verifica mensagem de tamanho
    assert "length must be less than or equal to" in data["error"]["message"]


def test_conversion_failure_returns_500(monkeypatch):
    # monkeypatch para simular falha interna de conversão
    def fake_convert(text):
        raise MarkdownConversionError("Simulated failure")
    monkeypatch.setattr(
        "app.services.markdown_converter.convert_markdown_to_html", fake_convert
    )
    payload = {"content": "# Test"}
    response = client.post("/convert/", json=payload)
    assert response.status_code == 500
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == 500
    assert "Simulated failure" in data["error"]["message"]
