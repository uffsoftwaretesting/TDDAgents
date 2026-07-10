import pytest
from fastapi import FastAPI


def test_app_main_importable():
    from app.main import app
    assert isinstance(app, FastAPI)


def test_config_module_importable():
    import app.config
    # deve existir o pacote config
    assert hasattr(app.config, '__spec__') or hasattr(app.config, '__path__')


def test_schemas_module_importable():
    import app.schemas
    assert hasattr(app.schemas, '__spec__') or hasattr(app.schemas, '__path__')


def test_services_module_importable():
    import app.services
    assert hasattr(app.services, '__spec__') or hasattr(app.services, '__path__')


def test_routes_module_importable():
    import app.routes
    assert hasattr(app.routes, '__spec__') or hasattr(app.routes, '__path__')
