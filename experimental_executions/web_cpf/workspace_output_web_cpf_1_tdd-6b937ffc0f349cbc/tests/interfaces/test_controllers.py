import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.application.use_cases.validate_cpf_use_case import ValidateCpfUseCase


class DummyUseCaseSuccess:
    def execute(self, cpf_str: str) -> bool:
        return True


class DummyUseCaseException:
    def execute(self, cpf_str: str) -> bool:
        raise Exception("Unexpected error")


@ pytest.fixture(autouse=True)
def clear_overrides():
    # Garante que não existam dependências sobrescritas de testes anteriores
    app.dependency_overrides = {}
    yield
    app.dependency_overrides = {}


def test_validate_cpf_success_status_200_and_valid_true():
    # Override da dependência para retornar sucesso
    dummy = DummyUseCaseSuccess()
    app.dependency_overrides[ValidateCpfUseCase] = lambda: dummy

    client = TestClient(app)
    response = client.post("/validate-cpf", json={"cpf": "529.982.247-25"})

    assert response.status_code == 200
    assert response.json() == {"valid": True}


def test_validate_cpf_payload_validation_returns_422():
    client = TestClient(app)
    # Campo ausente
    resp_missing = client.post("/validate-cpf", json={})
    assert resp_missing.status_code == 422

    # Tipo inválido (não string)
    resp_type = client.post("/validate-cpf", json={"cpf": 123})
    assert resp_type.status_code == 422


def test_validate_cpf_unexpected_exception_returns_500():
    # Override da dependência para lançar exceção genérica
    dummy = DummyUseCaseException()
    app.dependency_overrides[ValidateCpfUseCase] = lambda: dummy

    client = TestClient(app)
    response = client.post("/validate-cpf", json={"cpf": "52998224725"})

    assert response.status_code == 500
    # Detail padronizado de erro interno
    assert response.json().get("detail") == "Internal Server Error"