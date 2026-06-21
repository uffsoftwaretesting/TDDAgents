import pytest
from typing import Any
from fastapi.testclient import TestClient
from src.interfaces.api import app


client = TestClient(app)


@pytest.mark.parametrize(
    "cpf, expected", [
        ("52998224725", True),
        ("529.982.247-25", True),
    ],
)
def test_valid_cpf_returns_true(cpf: str, expected: bool) -> None:
    response = client.post(
        "/validate-cpf",
        json={"cpf": cpf},
    )
    assert response.status_code == 200
    assert response.json() == {"valid": expected}


@pytest.mark.parametrize(
    "cpf", [
        "00000000000",
        "000.000.000-00",
        "52998224724",
        "529.982.247-24",
    ],
)
def test_invalid_cpf_known_invalid_returns_false(cpf: str) -> None:
    response = client.post(
        "/validate-cpf",
        json={"cpf": cpf},
    )
    assert response.status_code == 200
    assert response.json() == {"valid": False}


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
def test_invalid_payload_raises_422(payload: dict[str, Any]) -> None:
    response = client.post(
        "/validate-cpf",
        json=payload,
    )
    assert response.status_code == 422
