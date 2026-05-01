import pytest
from fastapi.testclient import TestClient

from src.config.app import create_app, get_validate_usecase
from src.application.ports.validate_cpf_usecase import ValidateCpfUseCase


class ErrorUseCase(ValidateCpfUseCase):
    """
    Stub use case that always raises an unexpected error.
    """
    def execute(self, cpf: str) -> bool:
        raise RuntimeError("Unexpected error")


@pytest.fixture
def client_with_error():
    """
    TestClient configured to inject a use case that raises, capturing 500 responses.
    """
    app = create_app()
    # Override the use case dependency to our error-raising stub
    app.dependency_overrides[get_validate_usecase] = lambda: ErrorUseCase()
    # Prevent TestClient from raising server exceptions
    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_exception_returns_500(client_with_error):
    """
    When an internal exception occurs, the API should return HTTP 500
    with a generic JSON payload {'detail': 'Internal Server Error'}.
    """
    response = client_with_error.post(
        "/validate-cpf", json={"cpf": "529.982.247-25"}
    )
    assert response.status_code == 500
    # Ensure JSON content type
    assert response.headers.get("content-type", "").startswith("application/json")
    # Default FastAPI detail for unhandled errors
    assert response.json() == {"detail": "Internal Server Error"}
