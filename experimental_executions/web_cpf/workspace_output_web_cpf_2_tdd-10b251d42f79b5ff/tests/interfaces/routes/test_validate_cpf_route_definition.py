import inspect
import pytest
from fastapi import APIRouter


def test_route_module_and_router_exist():
    try:
        import interfaces.routes.validate_cpf as mod
    except ImportError:
        pytest.skip("interfaces.routes.validate_cpf module not found")

    # Ensure router exists
    assert hasattr(mod, 'router'), "APIRouter instance 'router' should be defined in interfaces.routes.validate_cpf"
    router = mod.router
    assert isinstance(router, APIRouter), "router should be an instance of APIRouter"

    # Check for POST /validate-cpf route
    routes = [r for r in router.routes if r.path == '/validate-cpf' and 'POST' in r.methods]
    assert routes, "Route POST /validate-cpf not found in router"
    route = routes[0]

    # Inspect endpoint signature
    endpoint = route.endpoint
    sig = inspect.signature(endpoint)
    params = list(sig.parameters.keys())
    assert 'input' in params, "Endpoint should accept 'input' parameter"
    assert 'use_case' in params, "Endpoint should accept 'use_case' parameter"

    # Check annotations
    from interfaces.schemas import CpfInput, CpfOutput
    from application.use_cases.validate_cpf_use_case import ValidateCpfUseCase

    assert sig.parameters['input'].annotation is CpfInput, "`input` parameter should be annotated with CpfInput"
    assert sig.parameters['use_case'].annotation is ValidateCpfUseCase, "`use_case` parameter should be annotated with ValidateCpfUseCase"
    # Return annotation
    assert sig.return_annotation is CpfOutput, "Endpoint should declare return annotation CpfOutput"