import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config.settings import get_settings

client = TestClient(app)


def test_openapi_json_contains_schemas_and_endpoint_details():
    response = client.get("/openapi.json")
    assert response.status_code == 200, "OpenAPI JSON endpoint should return 200"
    spec = response.json()

    # Components and schemas existence
    assert "components" in spec, "OpenAPI spec should have 'components'"
    schemas = spec["components"]["schemas"]
    for schema_name in ["MarkdownInput", "HTMLResponse", "ErrorResponse"]:
        assert schema_name in schemas, f"Schema '{schema_name}' not found in OpenAPI components"

    # Validate MarkdownInput schema properties
    mi = schemas["MarkdownInput"]
    assert mi.get("type") == "object", "MarkdownInput schema must be of type object"
    assert "properties" in mi and "content" in mi["properties"], "MarkdownInput must have 'content' property"
    assert "required" in mi and "content" in mi["required"], "MarkdownInput must require 'content'"
    assert mi["properties"]["content"]["type"] == "string", "MarkdownInput.content must be string"

    # Validate HTMLResponse schema basic structure
    hr = schemas["HTMLResponse"]
    assert hr.get("type") == "object", "HTMLResponse schema must be of type object"
    props = hr.get("properties", {})
    assert "success" in props and props["success"]["type"] == "boolean", "HTMLResponse.success must be boolean"
    assert "data" in props, "HTMLResponse must have 'data' property"

    # Validate endpoint metadata
    paths = spec.get("paths", {})
    assert "/convert/" in paths, "Convert endpoint '/convert/' not found in OpenAPI paths"
    post_op = paths["/convert/"]["post"]
    assert post_op.get("summary") == "Convert Markdown to HTML", "Endpoint summary incorrect"
    assert "description" in post_op and post_op["description"].startswith("Recebe"), "Endpoint description missing or incorrect"

    # Validate 200 response refers to HTMLResponse schema
    responses = post_op.get("responses", {})
    resp_200 = responses.get("200", {})
    content = resp_200.get("content", {}).get("application/json", {})
    schema_ref = content.get("schema", {}).get("$ref")
    assert schema_ref == "#/components/schemas/HTMLResponse", "200 response schema must reference HTMLResponse"


def test_docs_endpoint_serves_swagger_ui_and_contains_schemas():
    response = client.get("/docs")
    assert response.status_code == 200, "/docs endpoint should return 200"
    html = response.text

    # Basic Swagger UI markers
    assert "Swagger UI" in html or "swagger-ui" in html.lower(), "Swagger UI assets not found in /docs HTML"

    # The app title should appear in the HTML
    title = get_settings().app_name
    assert title in html, f"App title '{title}' not found in Swagger UI HTML"

    # The MarkdownInput schema name should be visible in Swagger UI
    assert "MarkdownInput" in html, "Schema 'MarkdownInput' not visible in Swagger UI"