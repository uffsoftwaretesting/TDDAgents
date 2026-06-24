import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import app.routes.markdown as markdown_route


def get_client():
    app = FastAPI()
    app.include_router(markdown_route.router)
    # Não propagar exceções do servidor para permitir capturar status 500
    return TestClient(app, raise_server_exceptions=False)


def test_convert_route_success():
    client = get_client()
    response = client.post("/convert", json={"markdown": "# Test"})
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
    assert "html" in body["data"]
    assert body["data"]["html"].strip() == "<h1>Test</h1>"
    assert body["error"] is None


def test_convert_route_missing_payload():
    client = get_client()
    response = client.post("/convert", json={})
    assert response.status_code == 422


def test_convert_route_empty_markdown():
    client = get_client()
    response = client.post("/convert", json={"markdown": ""})
    assert response.status_code == 422
    body = response.json()
    assert any(
        "Markdown content must not be empty." in err.get("msg", "")
        for err in body.get("detail", [])
    )


def test_convert_route_markdown_too_large():
    client = get_client()
    too_long = "a" * 10001
    response = client.post("/convert", json={"markdown": too_long})
    assert response.status_code == 422
    body = response.json()
    assert any(
        "Markdown content too large (max 10000 chars)." in err.get("msg", "")
        for err in body.get("detail", [])
    )


def test_convert_route_internal_server_error(monkeypatch):
    # Forçar erro interno lançando exceção imprevista
    def fake_convert(text: str):
        raise RuntimeError("unexpected error")
    monkeypatch.setattr(markdown_route, "convert_markdown_to_html", fake_convert)
    client = get_client()
    response = client.post("/convert", json={"markdown": "# Test"})
    assert response.status_code == 500
