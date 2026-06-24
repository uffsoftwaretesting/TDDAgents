import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routes.markdown import router

# Monta aplicação FastAPI para testes de integração
app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_convert_endpoint_success():
    response = client.post(
        "/convert",
        json={"markdown": "# Hello World"}
    )
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
    assert "html" in body["data"]
    assert body["data"]["html"].strip() == "<h1>Hello World</h1>"
    assert body["error"] is None


def test_convert_endpoint_missing_field():
    # Falta o campo required 'markdown'
    response = client.post("/convert", json={})
    assert response.status_code == 422


def test_convert_endpoint_empty_markdown():
    response = client.post(
        "/convert",
        json={"markdown": ""}
    )
    assert response.status_code == 422
    body = response.json()
    # Deve retornar erro de validação customizada (verifica substring)
    assert any(
        "Markdown content must not be empty." in err.get("msg", "")
        for err in body.get("detail", [])
    )
