import sys
import importlib
import types
import pytest


def test_valid_cpf_delegates_to_library(monkeypatch):
    # Stub validator returning True
    stub_validator = types.SimpleNamespace(validate=lambda cpf: True)
    dummy_lib = types.SimpleNamespace(CPF=lambda *args, **kwargs: stub_validator)
    # Inject dummy "validate_docbr" module before import
    monkeypatch.setitem(sys.modules, 'validate_docbr', dummy_lib)
    # Ensure our adapter module is reloaded with the stub
    module_name = 'infrastructure.validators.validate_docbr'
    if module_name in sys.modules:
        del sys.modules[module_name]
    module = importlib.import_module(module_name)
    importlib.reload(module)

    validator = module.LibraryCPFValidator()
    assert validator.is_valid("12345678909") is True


def test_invalid_cpf_delegates_to_library(monkeypatch):
    # Stub validator returning False
    stub_validator = types.SimpleNamespace(validate=lambda cpf: False)
    dummy_lib = types.SimpleNamespace(CPF=lambda *args, **kwargs: stub_validator)
    monkeypatch.setitem(sys.modules, 'validate_docbr', dummy_lib)
    module_name = 'infrastructure.validators.validate_docbr'
    if module_name in sys.modules:
        del sys.modules[module_name]
    module = importlib.import_module(module_name)
    importlib.reload(module)

    validator = module.LibraryCPFValidator()
    assert validator.is_valid("00000000000") is False
