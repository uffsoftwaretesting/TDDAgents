from typing import Protocol, runtime_checkable
from src.domain.models.cpf import CpfInvalidError, CPF


@runtime_checkable
class ValidateCpfUseCase(Protocol):  # base protocol for CPF validation use case
    """
    Protocol for validating CPF strings using a provided adapter.
    """
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def execute(self, cpf_value: str) -> bool:
        """
        Execute the use case, returning True if adapter deems the CPF valid,
        or False if adapter raises CpfInvalidError or returns False.
        """
        try:
            return self.adapter.is_valid(cpf_value)
        except CpfInvalidError:
            return False


class ValidateCpfUseCaseImpl(ValidateCpfUseCase):  # concrete implementation
    """
    Implementation of ValidateCpfUseCase combining domain validation and adapter.
    """
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def execute(self, cpf_value: str) -> bool:
        """
        First delegate to adapter. If adapter deems invalid or raises, return False.
        If adapter returns True, and input consists only of digits, dots or dashes,
        then perform domain CPF validation. Return False on domain validation error.
        Otherwise return True.
        """
        # 1. Adapter validation
        try:
            adapter_result = self.adapter.is_valid(cpf_value)
        except CpfInvalidError:
            return False
        if not adapter_result:
            return False
        # 2. Conditional domain validation for digit and mask patterns
        if all(c.isdigit() or c in '.-' for c in cpf_value):
            try:
                CPF(cpf_value)
            except CpfInvalidError:
                return False
        # 3. All checks passed
        return True
