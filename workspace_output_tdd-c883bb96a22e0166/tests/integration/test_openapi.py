import pytest
from fastapi.testclient import TestClient
from src.config.app import create_app

@pytest.fixture(scope="module")
def client():
    app = create_app()
    return TestClient(app)


def test_docs_endpoint_available(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    # basic check that HTML is returned
    assert "<html" in response.text.lower()


def test_openapi_json_available_and_metadata(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    # JSON content
    assert response.headers.get("content-type", "").startswith("application/json")
    spec = response.json()
    # Metadata checks
    assert spec.get("info", {}).get("title") == "API de Validação de CPF", \
        f"Expected API title 'API de Validação de CPF', got {spec.get('info', {}).get('title')}"
    assert spec.get("info", {}).get("version") == "1.0.0", \
        f"Expected API version '1.0.0', got {spec.get('info', {}).get('version')}"
    # Paths checks
    paths = spec.get("paths", {})
    assert "/validate-cpf" in paths, "Path '/validate-cpf' not found in OpenAPI spec"
    post_op = paths["/validate-cpf"].get("post")
    assert post_op is not None, "POST operation for '/validate-cpf' not defined"
    # Tags
    assert post_op.get("tags") == ["CPF"], \
        f"Expected tags ['CPF'] for POST /validate-cpf, got {post_op.get('tags')}"
    # Request body schema
    request_body = post_op.get("requestBody", {})
    app_json = request_body.get("content", {}).get("application/json", {})
    schema = app_json.get("schema", {})
    assert schema.get("title") == "ValidateCpfRequest", \
        f"Expected request schema title 'ValidateCpfRequest', got {schema.get('title')}"
    properties = schema.get("properties", {})
    assert "cpf" in properties, "Property 'cpf' missing in ValidateCpfRequest schema"
    required = schema.get("required", [])
    assert "cpf" in required, "'cpf' should be a required field in ValidateCpfRequest"
    # Response schema
    responses = post_op.get("responses", {})
    assert "200" in responses, "Response 200 not defined for POST /validate-cpf"
    resp_schema = responses["200"].get("content", {}).get("application/json", {}).get("schema", {})
    assert resp_schema.get("title") == "ValidateCpfResponse", \
        f"Expected response schema title 'ValidateCpfResponse', got {resp_schema.get('title')}"
    resp_props = resp_schema.get("properties", {})
    assert "valid" in resp_props, "Property 'valid' missing in ValidateCpfResponse schema"