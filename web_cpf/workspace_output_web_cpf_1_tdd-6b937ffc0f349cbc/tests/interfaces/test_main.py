from src.main import app


def test_app_instantiated():
    assert app is not None


def test_app_includes_validate_cpf_route():
    """
    Sanity check: ensure that the FastAPI app includes a POST route at /validate-cpf.
    """
    # Collect all registered route paths and methods
    routes = [(route.path, set(route.methods)) for route in app.routes]
    # Assert that /validate-cpf exists with at least the POST method
    assert any(
        path == "/validate-cpf" and "POST" in methods
        for path, methods in routes
    ), f"Expected '/validate-cpf' POST route in app.routes, found routes: {routes}"