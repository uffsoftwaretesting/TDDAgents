import inspect
import pytest
from pydantic import BaseModel


def test_schemas_module_and_models_exist():
    try:
        import interfaces.schemas as schemas
    except ImportError:
        pytest.skip("interfaces.schemas module not found")

    # Test CpfInput
    assert hasattr(schemas, 'CpfInput'), "CpfInput not defined in interfaces.schemas"
    CpfInput = schemas.CpfInput
    assert inspect.isclass(CpfInput), "CpfInput should be a class"
    assert issubclass(CpfInput, BaseModel), "CpfInput should inherit from BaseModel"
    fields_in = CpfInput.model_fields
    assert 'cpf' in fields_in, "CpfInput should define a 'cpf' field"
    assert fields_in['cpf'].annotation is str, "CpfInput.cpf should be annotated as str"

    # Test CpfOutput
    assert hasattr(schemas, 'CpfOutput'), "CpfOutput not defined in interfaces.schemas"
    CpfOutput = schemas.CpfOutput
    assert inspect.isclass(CpfOutput), "CpfOutput should be a class"
    assert issubclass(CpfOutput, BaseModel), "CpfOutput should inherit from BaseModel"
    fields_out = CpfOutput.model_fields
    assert 'valid' in fields_out, "CpfOutput should define a 'valid' field"
    assert fields_out['valid'].annotation is bool, "CpfOutput.valid should be annotated as bool"