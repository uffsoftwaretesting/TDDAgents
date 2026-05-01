import pytest
from fastapi.testclient import TestClient

from src.config.app import create_app


'teste,omitempty'
# Fixture que instancia o app e o cliente
@pytest.fixture(scope="module")
def client():
    app = create_app()
    return TestClient(app)


def test_missing_cpf_field_returns_422(client):
    response = client.post("/validate-cpf", json={})
    assert response.status_code == 422


def test_empty_cpf_string_returns_422(client):
    response = client.post("/validate-cpf", json={"cpf": ""})
    assert response.status_code == 422


def test_null_cpf_returns_422(client):
    response = client.post("/validate-cpf", json={"cpf": None})
    assert response.status_code == 422

@pytest.mark.parametrize(
    "cpf_value", 
    [
        "abc",
        "12a.34",
        "1234567890@",
        "*&^%",
        "123.456.789_09"
    ]
)
def test_invalid_cpf_characters_returns_422(client, cpf_value):
    response = client.post("/validate-cpf", json={"cpf": cpf_value})
    assert response.status_code == 422
