from abc import ABC, abstractmethod


class CPFValidator(ABC):
    """
    Interface for CPF validation.
    """

    @abstractmethod
    def is_valid(self, cpf: str) -> bool:
        """
        Validate the given CPF string.

        :param cpf: CPF string (possibly masked).
        :return: True if valid, False otherwise.
        """
        raise NotImplementedError