import pytest
import typing
from src.application.usecases.validate_cpf_usecase import ValidateCpfUseCase, ValidateCpfUseCaseImpl
from src.domain.models.cpf import CpfInvalidError

class AdapterStubAlwaysTrue:
    """
    Stub adapter that always returns True for validity.
    """
    def is_valid(self, cpf: str) -> bool:
        return True

class AdapterStubRaisesError:
    """
    Stub adapter that always raises CpfInvalidError.
    """
    def is_valid(self, cpf: str) -> bool:
        raise CpfInvalidError("adapter error")


def test_validate_cpf_use_case_is_protocol():
    """
    ValidateCpfUseCase should be defined as a typing.Protocol.
    """
    assert issubclass(ValidateCpfUseCase, typing.Protocol), \
        "ValidateCpfUseCase must inherit from typing.Protocol"


def test_validate_cpf_use_case_impl_subclasses_protocol():
    """
    ValidateCpfUseCaseImpl should implement the ValidateCpfUseCase protocol.
    """
    assert issubclass(ValidateCpfUseCaseImpl, ValidateCpfUseCase), \
        "ValidateCpfUseCaseImpl must be a subclass of ValidateCpfUseCase"


def test_impl_execute_returns_true_when_adapter_returns_true():
    """
    Given an adapter that returns True,
    ValidateCpfUseCaseImpl.execute should return True.
    """
    adapter = AdapterStubAlwaysTrue()
    use_case = ValidateCpfUseCaseImpl(adapter)
    assert use_case.execute("any-cpf") is True


def test_impl_execute_returns_false_when_adapter_raises_error():
    """
    Given an adapter that raises CpfInvalidError,
    ValidateCpfUseCaseImpl.execute should catch it and return False.
    """
    adapter = AdapterStubRaisesError()
    use_case = ValidateCpfUseCaseImpl(adapter)
    assert use_case.execute("any-cpf") is False


def test_impl_execute_returns_false_for_invalid_domain_cpf():
    """
    Even if adapter returns True, if the CPF raw value is invalid,
    creating CPF() will raise CpfInvalidError and execute should return False.
    """
    adapter = AdapterStubAlwaysTrue()
    use_case = ValidateCpfUseCaseImpl(adapter)
    # '123' is too short -> domain CPF raises
    assert use_case.execute("123") is False
