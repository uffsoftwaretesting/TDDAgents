from typing import Protocol, runtime_checkable

@runtime_checkable
class CPFValidatorAdapter(Protocol):
    """
    Protocol for CPF validation adapters.
    """
    def is_valid(self, cpf: str) -> bool:
        """Return True if the given CPF string is valid, otherwise False or raise CpfInvalidError."""
        ...
