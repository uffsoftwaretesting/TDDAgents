import inspect
import typing
import pytest

# The protocol to be implemented in src/infrastructure/
from src.infrastructure.cpf_validator_adapter_protocol import CPFValidatorAdapter


def test_cpf_validator_adapter_is_protocol():
    """
    CPFValidatorAdapter should be defined as a typing.Protocol.
    """
    assert issubclass(CPFValidatorAdapter, typing.Protocol), \
        "CPFValidatorAdapter must inherit from typing.Protocol"


def test_cpf_validator_adapter_has_is_valid_method_with_correct_signature():
    """
    The protocol should define an is_valid(cpf: str) -> bool method.
    """
    # Fetch the signature of the abstract method
    sig = inspect.signature(CPFValidatorAdapter.is_valid)
    params = list(sig.parameters.values())
    # Expect exactly self and cpf
    assert len(params) == 2, "is_valid must accept exactly two parameters: self and cpf"
    # Check parameter names and types
    assert params[0].name == 'self', "First parameter should be 'self'"
    assert params[1].name == 'cpf', "Second parameter should be named 'cpf'"
    # Annotations
    assert params[1].annotation == str, "Parameter 'cpf' must be annotated as str"
    assert sig.return_annotation == bool, "is_valid must return bool"


def test_stub_implementing_protocol_passes_instance_check():
    """
    A stub with the correct is_valid signature should be recognized as an instance of the protocol.
    """
    class AdapterStub:
        def is_valid(self, cpf: str) -> bool:
            return True

    stub = AdapterStub()
    # runtime_checkable required on protocol for isinstance to work
    assert isinstance(stub, CPFValidatorAdapter), \
        "An object with is_valid(cpf: str) -> bool should satisfy CPFValidatorAdapter"
