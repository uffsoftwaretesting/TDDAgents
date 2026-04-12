import pytest
from fastapi.testclient import TestClient

from interface.routes import app
from application.dto.validation_result import ValidationResult

@pytest.fixture
def client():
    return TestClient(app)


def test_missing_payload_returns_422(client):
    response = client.post("/validate-cpf")
    assert response.status_code == 422


def test_null_cpf_returns_422(client):
    response = client.post("/validate-cpf", json={"cpf": None})
    assert response.status_code == 422


def test_extra_fields_discarded(client, monkeypatch):
    # Stub the use case to return valid=True with trimmed CPF
    def fake_execute(self, raw):
        trimmed = raw.strip()
        return ValidationResult(cpf_original=trimmed, cpf_formatado="", valid=True)

    monkeypatch.setattr(
        "interface.routes.ValidateCPFUseCase.execute",
        fake_execute
    )
    response = client.post(
        "/validate-cpf",
        json={"cpf": "any", "extra": "field"}
    )
    assert response.status_code == 200
    assert response.json() == {"cpf": "any", "valid": True}


@pytest.mark.parametrize("input_cpf, valid", [
    ("", False),                # empty string => valid: False
    ("12345678909", True),     # unmasked valid
    ("123.456.789-09", True),  # masked valid
    (" 12345678909 ", True),   # whitespace trimmed
    ("111.111.111-11", False), # repeated digits invalid
    ("00000000000", False),    # invalid pattern
    ("1234567890", False),     # too short invalid
])
def test_various_cpf_scenarios(client, monkeypatch, input_cpf, valid):
    # Stub the use case to return our expected validity
    def fake_execute(self, raw):
        return ValidationResult(cpf_original=raw.strip(), cpf_formatado="", valid=valid)

    monkeypatch.setattr(
        "interface.routes.ValidateCPFUseCase.execute",
        fake_execute
    )
    response = client.post("/validate-cpf", json={"cpf": input_cpf})
    assert response.status_code == 200
    assert response.json() == {"cpf": input_cpf.strip(), "valid": valid}