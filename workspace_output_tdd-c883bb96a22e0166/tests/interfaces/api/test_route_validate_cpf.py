import pytest
from fastapi.testclient import TestClient

from src.config.app import create_app, get_validate_usecase
from src.application.ports.validate_cpf_usecase import ValidateCpfUseCase


class StubValidateCpfUseCase(ValidateCpfUseCase):
    """
    Stub implementation of ValidateCpfUseCase to simulate valid/invalid responses.
    """
    def __init__(self, result: bool):
        self.result = result
        self.called_with = None

    def execute(self, cpf: str) -> bool:
        self.called_with = cpf
        return self.result


def test_validate_cpf_route_returns_valid_true():
    # Arrange: stub use case that always returns True
    stub = StubValidateCpfUseCase(True)
    app = create_app()
    # Override dependency to inject our stub
    app.dependency_overrides[get_validate_usecase] = lambda: stub
    client = TestClient(app)
    test_cpf = "529.982.247-25"

    # Act
    response = client.post("/validate-cpf", json={"cpf": test_cpf})

    # Assert
    assert response.status_code == 200
    assert response.json() == {"valid": True}
    assert stub.called_with == test_cpf


def test_validate_cpf_route_returns_valid_false():
    # Arrange: stub use case that always returns False
    stub = StubValidateCpfUseCase(False)
    app = create_app()
    app.dependency_overrides[get_validate_usecase] = lambda: stub
    client = TestClient(app)
    test_cpf = "111.111.111-11"

    # Act
    response = client.post("/validate-cpf", json={"cpf": test_cpf})

    # Assert
    assert response.status_code == 200
    assert response.json() == {"valid": False}
    assert stub.called_with == test_cpf
