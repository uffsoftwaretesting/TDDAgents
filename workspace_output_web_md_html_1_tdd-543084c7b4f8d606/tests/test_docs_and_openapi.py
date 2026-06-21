import os
import pytest
from fastapi.testclient import TestClient

import main


def setup_env(monkeypatch):
    # Garante que as variáveis de ambiente necessárias para instanciar Settings estejam definidas
    monkeypatch.setenv("APP_NAME", "TestApp")
    monkeypatch.setenv("DEBUG", "True")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8000")


def test_docs_and_redoc_available(monkeypatch):
    """
    As rotas /docs e /redoc devem responder com status 200.
    """
    setup_env(monkeypatch)
    client = TestClient(main.app)
    for path in ["/docs", "/redoc"]:
        response = client.get(path)
        assert response.status_code == 200, f"{path} should be available"


def test_openapi_includes_markdown_tag(monkeypatch):
    """
    O JSON de OpenAPI deve conter a tag 'markdown' na lista de tags.
    """
    setup_env(monkeypatch)
    client = TestClient(main.app)
    response = client.get("/openapi.json")
    assert response.status_code == 200, "/openapi.json should be available"
    openapi = response.json()
    tags = openapi.get("tags", [])
    assert any(tag.get("name") == "markdown" for tag in tags), "Tag 'markdown' must be present in OpenAPI tags"
