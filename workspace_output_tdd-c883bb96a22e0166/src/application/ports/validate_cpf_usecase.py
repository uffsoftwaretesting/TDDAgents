from abc import ABC, abstractmethod


class ValidateCpfUseCase(ABC):
    """
    Port interface for the CPF validation use case.
    """

    @abstractmethod
    def execute(self, cpf: str) -> bool:
        """
        Execute the CPF validation logic.

        :param cpf: CPF string (possibly masked).
        :return: True if valid, False otherwise.
        """
        raise NotImplementedError
