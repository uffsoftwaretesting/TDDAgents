import pytest
from fastapi.testclient import TestClient

from src.presentation.app import app, get_validate_cpf_usecase


class DummyUseCaseSuccess:
    def execute(self, cpf_value: str) -> bool:
        return True


class DummyUseCaseFailure:
    def execute(self, cpf_value: str) -> bool:
        return False


def test_validate_cpf_success():
    # Override dependency with a use case that returns True
    app.dependency_overrides[get_validate_cpf_usecase] = lambda: DummyUseCaseSuccess()
    client = TestClient(app)

    response = client.post("/validate-cpf", json={"cpf": "any-value"})
    assert response.status_code == 200
    assert response.json() == {"valid": True}

    # Clear overrides
    app.dependency_overrides.clear()


def test_validate_cpf_failure():
    # Override dependency with a use case that returns False
    app.dependency_overrides[get_validate_cpf_usecase] = lambda: DummyUseCaseFailure()
    client = TestClient(app)

    response = client.post("/validate-cpf", json={"cpf": "any-value"})
    assert response.status_code == 200
    assert response.json() == {"valid": False}

    # Clear overrides
    app.dependency_overrides.clear()


@pytest.mark.parametrize("payload", [
    {},                # missing field
    {"cpf": None},    # null value
    {"cpf": 123},     # wrong type
])
def test_validate_cpf_invalid_payload(payload):
    # No override needed; validation error occurs before dependency resolution
    client = TestClient(app)

    response = client.post("/validate-cpf", json=payload)
    assert response.status_code == 422
