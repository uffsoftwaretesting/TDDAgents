import pytest

from src.domain.models.cpf import CpfInvalidError
from src.application.usecases.validate_cpf_usecase import ValidateCpfUseCase


class AdapterStubSuccess:
    """
    Stub adapter that always returns True for validity.
    """
    def is_valid(self, cpf: str) -> bool:
        return True


class AdapterStubError:
    """
    Stub adapter that always raises a CpfInvalidError.
    """
    def is_valid(self, cpf: str) -> bool:
        raise CpfInvalidError("invalid cpf")


def test_execute_returns_true_when_adapter_returns_true():
    """
    Given an adapter that returns True,
    when executing the use case,
    then execute should return True.
    """
    adapter = AdapterStubSuccess()
    use_case = ValidateCpfUseCase(adapter)

    result = use_case.execute("any-cpf-value")

    assert result is True


def test_execute_returns_false_when_adapter_raises_error():
    """
    Given an adapter that raises CpfInvalidError,
    when executing the use case,
    then execute should catch the error and return False.
    """
    adapter = AdapterStubError()
    use_case = ValidateCpfUseCase(adapter)

    result = use_case.execute("any-cpf-value")

    assert result is False
