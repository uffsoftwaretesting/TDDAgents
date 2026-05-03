from abc import ABC, abstractmethod


class IValidateCpfRepository(ABC):
    """
    Interface for CPF validation repository.
    """
    @abstractmethod
    def validate(self, cpf_str: str) -> bool:
        """
        Validates the given CPF string.
        Returns True if valid, False otherwise.
        """
        pass
