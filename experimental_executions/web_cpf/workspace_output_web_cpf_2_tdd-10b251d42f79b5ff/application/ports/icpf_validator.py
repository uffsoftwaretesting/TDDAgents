from typing import Protocol


class ICpfValidator(Protocol):
    """
    Interface (Port) for CPF validation.
    """
    def validate(self, cpf: str) -> bool:
        """Validate a CPF string, returning True if valid, False otherwise."""
        ...
