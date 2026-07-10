from abc import ABC, abstractmethod


class ICPFValidator(ABC):
    """
    Interface for CPF validator adapters.
    """
    @abstractmethod
    def is_valid(self, cpf: str) -> bool:
        """
        Return whether the given CPF string is valid according to business rules.
        """
        ...
