import pytest

from infrastructure.validators.base import CPFValidator


def test_interface_method_not_implemented():
    validator = CPFValidator()
    # The base interface should not implement is_valid, forcing subclasses to override
    with pytest.raises(NotImplementedError):
        validator.is_valid("any_cpf")
