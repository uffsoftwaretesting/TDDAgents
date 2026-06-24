import pytest
from fastapi.exceptions import RequestValidationError

from app.main import app
from app.config.settings import get_settings


def test_app_title_and_debug_configured():
    """
    The FastAPI app title and debug flag should come from Settings.
    """
    settings = get_settings()
    # The app title should match the configured application name
    assert app.title == settings.app_name, (
        f"Expected app.title to be '{settings.app_name}', got '{app.title}'"
    )
    # The debug flag on the app should match Settings.debug
    assert app.debug is settings.debug, (
        f"Expected app.debug to be {settings.debug}, got {app.debug}"
    )


def test_convert_router_registered():
    """
    The '/convert/' POST route should be registered by the markdown router.
    """
    # Collect all registered route paths
    paths = [route.path for route in app.routes]
    assert "/convert/" in paths, (
        f"Route '/convert/' not found in registered routes: {paths}"
    )

    # Verify that the '/convert/' route supports POST
    convert_route = next((r for r in app.routes if r.path == "/convert/"), None)
    assert convert_route is not None, "Convert route not found"
    assert "POST" in convert_route.methods, (
        f"Expected 'POST' in methods of '/convert/' route, got {convert_route.methods}"
    )


def test_exception_handlers_registered():
    """
    Exception handlers for RequestValidationError and generic Exception must be registered.
    """
    handlers = app.exception_handlers
    assert RequestValidationError in handlers, (
        "RequestValidationError handler not registered in app.exception_handlers"
    )
    assert Exception in handlers, (
        "Generic Exception handler not registered in app.exception_handlers"
    )