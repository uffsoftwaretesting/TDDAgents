import pytest
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.domain.models import CPFValidation
from src.api.validation_router import (
    router,
    get_validate_usecase,
    get_retrieve_usecase,
    get_list_usecase,
)

@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router)
    return app


class DummyValidateUseCase:
    def __init__(self, result: CPFValidation):
        self._result = result

    async def execute(self, cpf: str) -> CPFValidation:
        return self._result


class DummyRetrieveUseCase:
    def __init__(self, result):
        self._result = result

    async def execute(self, cpf: str):
        return self._result


class DummyListUseCase:
    def __init__(self, result_list):
        self._result_list = result_list

    async def execute(self, page: int, size: int):
        return self._result_list


@pytest.mark.asyncio
async def test_post_validate_success(app):
    # Arrange
    input_cpf = "12345678901"
    validation = CPFValidation(
        id=uuid.uuid4(),
        cpf=input_cpf,
        is_valid=True,
        created_at=datetime.now(timezone.utc),
    )
    app.dependency_overrides[get_validate_usecase] = lambda: DummyValidateUseCase(validation)

    # Act
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/validate", json={"cpf": input_cpf}
        )

    # Assert
    assert response.status_code == 201
    data = response.json()
    assert data["cpf"] == input_cpf
    assert data["is_valid"] is True
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_post_validate_validation_error(app):
    # Malformed CPF → 422
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/validate", json={"cpf": "123"}
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_validate_not_found(app):
    # Arrange: use case returns None → 404
    app.dependency_overrides[get_retrieve_usecase] = lambda: DummyRetrieveUseCase(None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/validate/00000000000")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_validate_success(app):
    # Arrange
    cpf = "12345678901"
    validation = CPFValidation(
        id=uuid.uuid4(),
        cpf=cpf,
        is_valid=False,
        created_at=datetime.now(timezone.utc),
    )
    app.dependency_overrides[get_retrieve_usecase] = lambda: DummyRetrieveUseCase(validation)

    # Act
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/validate/{cpf}")

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["cpf"] == cpf
    assert data["is_valid"] is False


@pytest.mark.asyncio
async def test_get_validations_success(app):
    # Arrange
    page, size = 2, 3
    now = datetime.now(timezone.utc)
    item1 = CPFValidation(
        id=uuid.uuid4(), cpf='11111111111', is_valid=True, created_at=now
    )
    item2 = CPFValidation(
        id=uuid.uuid4(), cpf='22222222222', is_valid=False, created_at=now
    )
    app.dependency_overrides[get_list_usecase] = lambda: DummyListUseCase([item1, item2])

    # Act
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/validations?page={page}&size={size}")

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == page
    assert data["size"] == size
    assert isinstance(data["items"], list)
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_get_validations_validation_error(app):
    # page and size must be ≥1 → 422
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/validations?page=0&size=0")
    assert response.status_code == 422
