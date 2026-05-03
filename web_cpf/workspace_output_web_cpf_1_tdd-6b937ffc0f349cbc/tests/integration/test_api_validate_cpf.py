import pytest
from httpx import AsyncClient, ASGITransport
from src.main import app
import validate_docbr

@pytest.mark.asyncio
async def test_missing_cpf_field_returns_422():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/validate-cpf", json={})
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_empty_cpf_returns_422():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/validate-cpf", json={"cpf": ""})
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_invalid_characters_in_cpf_returns_422():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/validate-cpf", json={"cpf": "123.456.78a-09"})
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_valid_cpf_masked_returns_true():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/validate-cpf", json={"cpf": "529.982.247-25"})
    assert response.status_code == 200
    assert response.json() == {"valid": True}

@pytest.mark.asyncio
async def test_valid_cpf_unmasked_returns_true():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/validate-cpf", json={"cpf": "52998224725"})
    assert response.status_code == 200
    assert response.json() == {"valid": True}

@pytest.mark.asyncio
async def test_all_digits_equal_returns_false():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/validate-cpf", json={"cpf": "111.111.111-11"})
    assert response.status_code == 200
    assert response.json() == {"valid": False}

@pytest.mark.asyncio
async def test_invalid_check_digits_returns_false():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/validate-cpf", json={"cpf": "529.982.247-24"})
    assert response.status_code == 200
    assert response.json() == {"valid": False}

@pytest.mark.asyncio
async def test_adapter_failure_returns_500(monkeypatch):
    # Simula falha no adapter externo
    def fake_validate(self, cpf):
        raise Exception("External adapter failure")
    monkeypatch.setattr(validate_docbr.CPF, "validate", fake_validate)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/validate-cpf", json={"cpf": "52998224725"})
    assert response.status_code == 500
    assert response.json().get("detail") == "Internal Server Error"
