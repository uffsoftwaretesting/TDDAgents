import pytest
from typing import Any
from fastapi.testclient import TestClient
from src.interfaces.api import app, get_validate_cpf_use_case


class FakeUseCaseTrue:
    """Fake use case que sempre retorna True."""

    def execute(self, cpf: str) -> bool:
        return True


class FakeUseCaseFalse:
    """Fake use case que sempre retorna False."""

    def execute(self, cpf: str) -> bool:
        return False


@pytest.fixture(autouse=True)
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize(
    "cpf, usecase_cls, expected", [
        ("12345678909", FakeUseCaseTrue, True),
        ("123.456.789-09", FakeUseCaseTrue, True),
        ("52998224725", FakeUseCaseFalse, False),
        ("529.982.247-25", FakeUseCaseFalse, False),
    ],
)
def test_validate_cpf_valid_payloads(
    cpf: str,
    usecase_cls: type,
    expected: bool,
    client: TestClient,
) -> None:
    fake = usecase_cls()
    app.dependency_overrides[get_validate_cpf_use_case] = lambda: fake
    response = client.post(
        "/validate-cpf",
        json={"cpf": cpf},
    )
    assert response.status_code == 200
    assert response.json() == {"valid": expected}
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "payload", [
        {},
        {"cpf": ""},
        {"cpf": "1234567890"},
        {"cpf": "1" * 15},
        {"cpf": "1234567890A"},
        {"cpf": None},
        {"cpf": "   "},
        {"cpf": "123-456.789/09"},
    ],
)
def test_validate_cpf_invalid_payloads(
    payload: dict[str, Any],
    client: TestClient,
) -> None:
    response = client.post(
        "/validate-cpf",
        json=payload,
    )
    assert response.status_code == 422
