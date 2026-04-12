import pytest
from fastapi.testclient import TestClient

import core.app as core_app_module
from core.domain.errors import DomainError
from application.dto.validation_result import ValidationResult

app = core_app_module.app

@pytest.fixture
def client():
    return TestClient(app)


def test_missing_payload_returns_422(client):
    response = client.post("/validate-cpf")
    assert response.status_code == 422


def test_null_cpf_returns_422(client):
    response = client.post("/validate-cpf", json={"cpf": None})
    assert response.status_code == 422


def test_successful_execute_returns_valid_true(client, monkeypatch):
    # Stub the use case to return a successful ValidationResult
    def fake_execute(self, raw):
        trimmed = raw.strip()
        return ValidationResult(cpf_original=trimmed, cpf_formatado="", valid=True)

    monkeypatch.setattr(
        "core.app.ValidateCPFUseCase.execute",
        fake_execute,
    )

    response = client.post("/validate-cpf", json={"cpf": " 12345678909 "})
    assert response.status_code == 200
    # trimmed input
    assert response.json() == {"cpf": "12345678909", "valid": True}


def test_domain_error_propagates_to_custom_handler(client, monkeypatch):
    # Stub the use case to raise DomainError
    def fake_execute(self, raw):
        raise DomainError("domain failure")

    monkeypatch.setattr(
        "core.app.ValidateCPFUseCase.execute",
        fake_execute,
    )

    response = client.post("/validate-cpf", json={"cpf": "zzzzz"})
    assert response.status_code == 200
    # Even though DomainError was raised, our exception handler should catch and return valid:false
    assert response.json() == {"cpf": "zzzzz", "valid": False}