import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_post_convert_success():
    # Dado um markdown válido
    payload = {"content": "# Hello World"}
    # Quando fazemos POST em /convert/
    response = client.post("/convert/", json=payload)
    # Então recebemos 200 e HTML convertido
    assert response.status_code == 200
    json_data = response.json()
    assert json_data.get("success") is True
    assert "data" in json_data
    # Verifica que o html contém a tag <h1>
    html = json_data["data"].get("html")
    assert isinstance(html, str)
    assert "<h1>Hello World</h1>" in html


@pytest.mark.parametrize("invalid_payload", [
    {},                     # payload vazio ou campo ausente
    {"cont": "# Missing"},  # chave errada
    {"content": ""},     # vazio
    {"content": "   "},  # apenas whitespace
    {"content": 123},      # tipo errado
])
def test_post_convert_unprocessable_entity(invalid_payload):
    # Quando payload é inválido
    response = client.post("/convert/", json=invalid_payload)
    # Então retornamos 422
    assert response.status_code == 422
    json_data = response.json()
    assert json_data.get("success") is False
    # Verifica estrutura de erro
    assert "error" in json_data
    error = json_data["error"]
    assert isinstance(error, dict)
    assert error.get("code") == 422
    assert isinstance(error.get("message"), str)
