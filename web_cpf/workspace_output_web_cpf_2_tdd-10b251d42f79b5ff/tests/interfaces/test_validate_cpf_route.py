import pytest
from fastapi.testclient import TestClient

from interfaces.main import app

client = TestClient(app)

@ pytest.mark.parametrize("cpf", [
    "529.982.247-25",  # válido com máscara
    "52998224725"      # válido sem máscara
])
def test_validate_cpf_valid(cpf):
    response = client.post("/validate-cpf", json={"cpf": cpf})
    assert response.status_code == 200
    data = response.json()
    assert "valid" in data
    assert data["valid"] is True

@ pytest.mark.parametrize("payload", [
    None,              # sem body
    {},                # JSON vazio
    {"wrong": "x"}  # campo ausente
])
def test_validate_cpf_payload_malformed_or_missing(payload):
    if payload is None:
        response = client.post("/validate-cpf")
    else:
        response = client.post("/validate-cpf", json=payload)
    assert response.status_code == 422

@ pytest.mark.parametrize("cpf", [
    "",             # string vazia
    "123",          # muito curto
    "abc",          # caracteres não-númericos
    "11111111111"   # dígitos iguais (inválido)
])
def test_validate_cpf_invalid_formats_return_false(cpf):
    response = client.post("/validate-cpf", json={"cpf": cpf})
    assert response.status_code == 200
    data = response.json()
    assert "valid" in data
    assert data["valid"] is False
