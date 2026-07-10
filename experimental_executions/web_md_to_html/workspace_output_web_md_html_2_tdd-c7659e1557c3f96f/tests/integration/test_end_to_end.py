import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from app.routes.markdown import router

@pytest_asyncio.fixture
def app():
    """Construct the FastAPI application for testing."""
    app = FastAPI()
    app.include_router(router)
    return app

@pytest_asyncio.fixture
async def client(app):
    """Provide an HTTPX AsyncClient against the FastAPI app using ASGITransport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.mark.asyncio
async def test_convert_endpoint_success(client):
    response = await client.post(
        "/convert", json={"markdown": "# Hello World"}
    )
    assert response.status_code == 200
    body = response.json()
    assert "data" in body and "html" in body["data"]
    # The HTML should match the heading conversion
    assert body["data"]["html"].strip() == "<h1>Hello World</h1>"
    assert body.get("error") is None

@pytest.mark.asyncio
async def test_convert_endpoint_missing_field(client):
    # Missing required 'markdown' field should 422
    response = await client.post("/convert", json={})
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_convert_endpoint_empty_markdown(client):
    # Empty markdown triggers custom validation
    response = await client.post(
        "/convert", json={"markdown": ""}
    )
    assert response.status_code == 422
    detail = response.json().get("detail", [])
    assert any(
        "Markdown content must not be empty." in err.get("msg", "")
        for err in detail
    )

@pytest.mark.asyncio
async def test_convert_endpoint_markdown_too_large(client):
    # Payload exceeding 10000 chars triggers size validation
    too_long = "a" * 10001
    response = await client.post(
        "/convert", json={"markdown": too_long}
    )
    assert response.status_code == 422
    detail = response.json().get("detail", [])
    assert any(
        "Markdown content too large (max 10000 chars)." in err.get("msg", "")
        for err in detail
    )

@pytest.mark.asyncio
async def test_docs_endpoint(client):
    # /docs should serve the Swagger UI HTML
    response = await client.get("/docs")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "text/html" in content_type
    # Basic check for HTML tags
    assert "<html" in response.text.lower()

@pytest.mark.asyncio
async def test_openapi_endpoint(client):
    # /openapi.json should return the OpenAPI spec
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    body = response.json()
    assert "openapi" in body
    # Validate that it's OpenAPI 3.x or 3.y
    assert isinstance(body["openapi"], str) and body["openapi"].startswith("3.")