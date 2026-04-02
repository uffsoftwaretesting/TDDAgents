import pytest
import inspect

from src.interfaces.repositories.cpf_validation_repository import CPFValidationRepository

@ pytest.mark.asyncio
async def test_methods_are_defined_and_async():
    methods = {
        'save': ['validation'],
        'get_by_cpf': ['cpf'],
        'list_all': ['page', 'size'],
    }

    for method_name, expected_params in methods.items():
        # Check method existence
        assert hasattr(CPFValidationRepository, method_name), f"Method '{method_name}' must be defined"
        method = getattr(CPFValidationRepository, method_name)
        # Check it's async
        assert inspect.iscoroutinefunction(method), f"Method '{method_name}' must be an async function"
        # Check signature parameters
        sig = inspect.signature(method)
        param_names = list(sig.parameters.keys())
        # strip 'self'
        if param_names and param_names[0] == 'self':
            param_names = param_names[1:]
        assert param_names == expected_params, (
            f"Method '{method_name}' parameters should be {expected_params}, got {param_names}"
        )

@ pytest.mark.asyncio
async def test_default_methods_raise_not_implemented():
    repo = CPFValidationRepository()
    # save
    with pytest.raises(NotImplementedError):
        await repo.save(None)
    # get_by_cpf
    with pytest.raises(NotImplementedError):
        await repo.get_by_cpf("12345678901")
    # list_all
    with pytest.raises(NotImplementedError):
        await repo.list_all(1, 10)
