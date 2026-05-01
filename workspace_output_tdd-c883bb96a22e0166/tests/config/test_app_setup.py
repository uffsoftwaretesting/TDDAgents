import pytest
from fastapi import FastAPI, Depends
from fastapi.routing import APIRoute

import src.config.app as app_module

def test_provider_functions_exist():
    # Providers de DI devem existir
    assert callable(getattr(app_module, 'get_cpf_validator', None)), \
        "Provider 'get_cpf_validator' não encontrado em src.config.app"
    assert callable(getattr(app_module, 'get_validate_usecase', None)), \
        "Provider 'get_validate_usecase' não encontrado em src.config.app"


def test_create_app_returns_fastapi_instance():
    # A factory deve retornar uma instância de FastAPI
    app = app_module.create_app()
    assert isinstance(app, FastAPI), "create_app deve retornar um objeto FastAPI"


def test_validate_cpf_route_configured():
    # A rota '/validate-cpf' deve estar registrada com método POST e dependências
    app = app_module.create_app()
    route = next((r for r in app.routes if isinstance(r, APIRoute) and r.path == '/validate-cpf'), None)
    assert route is not None, "Rota '/validate-cpf' não encontrada"
    assert 'POST' in route.methods, "Rota '/validate-cpf' deve suportar o método POST"

    # Verifica dependências configuradas via Depends
    dep_calls = [dep.call for dep in route.dependant.dependencies]
    dep_names = [getattr(callable, '__name__', None) for callable in dep_calls]
    assert 'get_cpf_validator' in dep_names, \
        "Dependência 'get_cpf_validator' não configurada para '/validate-cpf'"
    assert 'get_validate_usecase' in dep_names, \
        "Dependência 'get_validate_usecase' não configurada para '/validate-cpf'"