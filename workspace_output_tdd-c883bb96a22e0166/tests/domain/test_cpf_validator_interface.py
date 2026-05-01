import inspect
import pytest

from src.domain.ports.cpf_validator import CPFValidator


def test_cpf_validator_interface_exists():
    """
    Sanity check: CPFValidator interface must exist as a class.
    """
    assert isinstance(CPFValidator, type), "CPFValidator should be a class"


def test_cpf_validator_has_is_valid_method():
    """
    CPFValidator must declare an 'is_valid' method accepting 'cpf' and returning bool.
    """
    # Ensure method exists
    assert hasattr(CPFValidator, 'is_valid'), "CPFValidator should have a method named 'is_valid'"
    method = getattr(CPFValidator, 'is_valid')
    # Validate it's callable
    assert callable(method), "CPFValidator.is_valid should be callable"

    # Inspect signature
    sig = inspect.signature(method)
    params = list(sig.parameters.keys())
    # Expecting 'self' and 'cpf'
    assert params == ['self', 'cpf'], (
        f"Expected parameters ['self', 'cpf'], got {params}"
    )
    # Check return annotation
    return_ann = sig.return_annotation
    assert return_ann == bool, (
        f"Expected return annotation 'bool' for is_valid, got {return_ann}"
    )