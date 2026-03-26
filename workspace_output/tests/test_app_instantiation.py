import pytest
from fastapi import FastAPI

def test_app_instantiation():
    # Tenta importar a aplicação e verificar se há uma instância do FastAPI
    from api.main import app
    assert isinstance(app, FastAPI)
