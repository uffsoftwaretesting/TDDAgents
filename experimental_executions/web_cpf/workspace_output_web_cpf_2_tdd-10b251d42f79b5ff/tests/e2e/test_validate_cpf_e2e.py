import pytest
from fastapi.testclient import TestClient
from interfaces.main import app
import infrastructure.cpf_validator_adapter as adapter_module

client = TestClient(app)

@ pytest.mark.parametrize("cpf, expected_valid", [
    ("529.982.247-25", True),
    ("52998224725", True),
    ("", False),
    ("123", False),
    ("abc", False),
    ("11111111111", False),
])
def test_e2e_validate_cpf_various(cpf, expected_valid):
    """
    Testa diversos valores de CPF válidos e inválidos via E2E.
    """
    response = client.post("/validate-cpf", json={"cpf": cpf})
    assert response.status_code == 200
    data = response.json()
    assert "valid" in data
    assert data["valid"] is expected_valid

@ pytest.mark.parametrize("payload", [
    None,
    {},
    {"wrong": "value"},
    {"cpf": 123},  # tipo incorreto
])
def test_e2e_validate_cpf_payload_errors(payload):
    """
    Payload ausente, vazio, campo errado ou tipo incorreto deve gerar 422.
    """
    if payload is None:
        response = client.post("/validate-cpf")
    else:
        response = client.post("/validate-cpf", json=payload)
    assert response.status_code == 422

def test_e2e_internal_validator_exception(monkeypatch):
    """
    Simula falha interna na biblioteca externa e garante que a API retorne valid=False.
    """
    # DummyCPF que sempre lança exceção
    class DummyCPF:
        def validate(self, cpf: str) -> bool:
            raise Exception("internal lib error")
    # Substitui o CPF usado pelo adapter
    monkeypatch.setattr(adapter_module, 'CPF', DummyCPF)
    response = client.post("/validate-cpf", json={"cpf": "529.982.247-25"})
    assert response.status_code == 200
    data = response.json()
    assert "valid" in data
    assert data["valid"] is False
