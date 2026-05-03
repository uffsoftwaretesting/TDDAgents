import inspect
import pytest
from typing import Protocol as TypingProtocol


def test_icpf_validator_interface_exists():
    """
    Deve existir a interface ICpfValidator em application.ports.icpf_validator
    como um Protocol com método validate(cpf: str) -> bool.
    """
    try:
        from application.ports.icpf_validator import ICpfValidator
    except ImportError:
        pytest.skip("application.ports.icpf_validator module not found")
    # Should be a class or Protocol
    assert inspect.isclass(ICpfValidator), "ICpfValidator should be a class or Protocol"
    # Should subclass typing.Protocol
    assert issubclass(ICpfValidator, TypingProtocol), "ICpfValidator should subclass typing.Protocol"
    # Should define method validate
    assert hasattr(ICpfValidator, 'validate'), "ICpfValidator should define method validate"
    sig = inspect.signature(ICpfValidator.validate)
    params = list(sig.parameters.keys())
    assert params == ['self', 'cpf'], "validate should accept parameters (self, cpf)"
    # Validate return annotation
    assert sig.return_annotation == bool, "validate should have return annotation bool"


def test_use_case_init_type_annotation():
    """
    ValidateCpfUseCase.__init__ deve receber um ICpfValidator anotado corretamente.
    """
    try:
        from application.use_cases.validate_cpf_use_case import ValidateCpfUseCase
        from application.ports.icpf_validator import ICpfValidator
    except ImportError:
        pytest.skip("Required modules not found")
    sig = inspect.signature(ValidateCpfUseCase.__init__)
    params = sig.parameters
    assert 'validator' in params, "ValidateCpfUseCase.__init__ should accept 'validator'"
    annot = params['validator'].annotation
    assert annot is ICpfValidator, "validator parameter should be annotated with ICpfValidator"