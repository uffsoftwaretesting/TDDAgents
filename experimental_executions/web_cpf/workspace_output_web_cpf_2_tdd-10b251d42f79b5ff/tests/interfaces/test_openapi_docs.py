import pytest
from fastapi.testclient import TestClient
from interfaces.main import app

client = TestClient(app)


def test_openapi_json_contains_validate_cpf():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert "paths" in data
    # Verifica se o caminho /validate-cpf está documentado
    assert "/validate-cpf" in data["paths"]
    post_op = data["paths"]["/validate-cpf"].get("post")
    assert post_op is not None, "Operação POST /validate-cpf deve existir"
    # Verifica requestBody e responses
    assert "requestBody" in post_op, "POST /validate-cpf deve definir requestBody"
    assert "200" in post_op["responses"], "POST /validate-cpf deve ter resposta 200"
    # Verifica esquemas dos modelos
    assert "components" in data, "OpenAPI deve definir components"
    schemas = data["components"].get("schemas", {})
    assert "CpfInput" in schemas, "Schema CpfInput deve existir nos components"
    assert "CpfOutput" in schemas, "Schema CpfOutput deve existir nos components"


def test_swagger_ui_available():
    response = client.get("/docs")
    assert response.status_code == 200
    text = response.text.lower()
    assert "swagger-ui" in text or "swagger ui" in text, "UI do Swagger deve estar disponível em /docs"


def test_redoc_ui_available():
    response = client.get("/redoc")
    assert response.status_code == 200
    text = response.text.lower()
    assert "redoc" in text, "UI do ReDoc deve estar disponível em /redoc"