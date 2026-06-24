import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


@pytest.mark.asyncio
async def test_convert_markdown_success():
    payload = {"markdown": "# Hello"}
    response = client.post("/convert-markdown", json=payload)
    assert response.status_code == 200
    data = response.json()
    # Expect HTML conversion and success message
    assert data.get("html") == "<h1>Hello</h1>"
    assert data.get("message") == "Conversion successful"


@pytest.mark.asyncio
async def test_convert_markdown_validation_error_empty_markdown():
    # Empty markdown should trigger a 422 Unprocessable Entity
    payload = {"markdown": ""}
    response = client.post("/convert-markdown", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_convert_markdown_validation_error_missing_field():
    # Missing markdown field should trigger a 422 Unprocessable Entity
    payload = {}
    response = client.post("/convert-markdown", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_convert_markdown_internal_error(monkeypatch):
    # Simula falha interna na conversão para gerar 500 Internal Server Error
    from services.markdown_converter import MarkdownConversionError

    def fake_convert(text: str):
        raise MarkdownConversionError("Internal conversion error")

    # Substitui a função convert usada pela rota
    monkeypatch.setattr("routes.markdown.convert", fake_convert)
    
    payload = {"markdown": "# Hello"}
    response = client.post("/convert-markdown", json=payload)
    assert response.status_code == 500
    data = response.json()
    # Verifica se retorna o ErrorModel com detail e code corretos
    assert data == {"detail": "Internal conversion error", "code": 5001}