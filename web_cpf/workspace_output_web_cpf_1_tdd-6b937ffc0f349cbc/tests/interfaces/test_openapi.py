from fastapi.testclient import TestClient
from src.main import app

client = TestClient(app)

def test_docs_route_available():
    """
    The Swagger UI should be available at /docs and contain expected UI title.
    """
    response = client.get("/docs")
    assert response.status_code == 200
    # Check for a known element in Swagger UI page
    assert "Swagger UI" in response.text or "swagger-ui" in response.text.lower()


def test_openapi_json_available():
    """
    The OpenAPI schema JSON should be available at /openapi.json with basic structure.
    """
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    # Top-level OpenAPI fields
    assert data.get("openapi", "").startswith("3."), "Expected OpenAPI version declaration"
    assert "paths" in data and isinstance(data["paths"], dict)


def test_validate_cpf_path_in_openapi():
    """
    The /validate-cpf POST operation must be documented with correct request and response schemas.
    """
    data = client.get("/openapi.json").json()
    paths = data["paths"]
    # Ensure the path is registered
    assert "/validate-cpf" in paths, f"'/validate-cpf' not found in OpenAPI paths: {list(paths.keys())}"
    post_op = paths["/validate-cpf"].get("post")
    assert post_op is not None, "POST operation for /validate-cpf is missing"

    # Check request body schema reference
    request_body = post_op.get("requestBody")
    assert request_body is not None, "requestBody is missing in POST /validate-cpf"
    content = request_body.get("content", {})
    assert "application/json" in content, "application/json content type missing in requestBody"
    schema = content["application/json"]["schema"]
    # Should reference the ValidateCpfRequest model
    ref = schema.get("$ref")
    expected_req_ref = "#/components/schemas/ValidateCpfRequest"
    assert ref == expected_req_ref, f"Expected request schema $ref '{expected_req_ref}', got '{ref}'"

    # Check 200 response schema reference
    responses = post_op.get("responses", {})
    resp_200 = responses.get("200")
    assert resp_200 is not None, "200 response is missing for POST /validate-cpf"
    resp_content = resp_200.get("content", {})
    assert "application/json" in resp_content, "application/json missing in 200 response content"
    resp_schema = resp_content["application/json"]["schema"]
    resp_ref = resp_schema.get("$ref")
    expected_resp_ref = "#/components/schemas/ValidateCpfResponse"
    assert resp_ref == expected_resp_ref, f"Expected response schema $ref '{expected_resp_ref}', got '{resp_ref}'"