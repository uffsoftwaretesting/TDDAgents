import pytest
from datetime import datetime, timezone

from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport
from api.main import app

from application.usecases.validate_cpf_usecase import ValidateCPFUseCase
from application.usecases.get_cpf_history_usecase import (
    GetCPFHistoryUseCase,
    CPFHistoryDTO,
    ValidationEntryDTO,
)
from application.usecases.list_validations_usecase import (
    ListValidationsUseCase,
    PaginatedValidationsDTO,
    ValidationRecordDTO,
)


@pytest.mark.asyncio
async def test_post_validate_cpf_success(monkeypatch):
    # Stub the usecase to return a positive result
    async def mock_execute(self, cpf_str):
        return type("X", (), {"cpf": cpf_str, "valid": True})()
    monkeypatch.setattr(ValidateCPFUseCase, "execute", mock_execute)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/cpf/validate", json={"cpf": "12345678901"})
    assert response.status_code == 200
    assert response.json() == {"cpf": "12345678901", "valid": True}


@pytest.mark.asyncio
async def test_post_validate_cpf_invalid_payload():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # cpf too short
        response = await client.post("/cpf/validate", json={"cpf": "123"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_validate_cpf_db_error(monkeypatch):
    # Stub usecase to raise
    async def mock_execute(self, cpf_str):
        raise RuntimeError("DB error simulated")
    monkeypatch.setattr(ValidateCPFUseCase, "execute", mock_execute)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/cpf/validate", json={"cpf": "12345678901"})
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_get_cpf_history_success(monkeypatch):
    # Create fake entries
    ts = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    entry = ValidationEntryDTO(timestamp=ts, valid=False)
    async def mock_execute(self, cpf_str):
        return CPFHistoryDTO(cpf=cpf_str, results=[entry])
    monkeypatch.setattr(GetCPFHistoryUseCase, "execute", mock_execute)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/cpf/12345678901/history")
    assert response.status_code == 200
    assert response.json() == {
        "cpf": "12345678901",
        "results": [{"timestamp": ts.isoformat(), "valid": False}],
    }


@pytest.mark.asyncio
async def test_get_cpf_history_invalid_param():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/cpf/abc/history")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_cpf_history_db_error(monkeypatch):
    async def mock_execute(self, cpf_str):
        raise RuntimeError("DB fail")
    monkeypatch.setattr(GetCPFHistoryUseCase, "execute", mock_execute)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/cpf/12345678901/history")
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_get_all_history_success(monkeypatch):
    # Create fake record
    ts = datetime(2024, 2, 2, 15, 30, tzinfo=timezone.utc)
    rec = ValidationRecordDTO(id=5, cpf="12345678901", valid=True, timestamp=ts)
    async def mock_execute(self, page, size):
        return PaginatedValidationsDTO(items=[rec], page=page, size=size, total=1)
    monkeypatch.setattr(ListValidationsUseCase, "execute", mock_execute)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/cpf/history?page=2&size=3")
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"id": 5, "cpf": "12345678901", "valid": True, "timestamp": ts.isoformat()}
        ],
        "page": 2,
        "size": 3,
        "total": 1,
    }


@pytest.mark.asyncio
async def test_get_all_history_invalid_query_params():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/cpf/history?page=0&size=1")
        r2 = await client.get("/cpf/history?page=1&size=0")
    assert r1.status_code == 422
    assert r2.status_code == 422


@pytest.mark.asyncio
async def test_get_all_history_db_error(monkeypatch):
    async def mock_execute(self, page, size):
        raise Exception("list failure")
    monkeypatch.setattr(ListValidationsUseCase, "execute", mock_execute)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/cpf/history?page=1&size=10")
    assert response.status_code == 500
