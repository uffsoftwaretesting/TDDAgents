from abc import ABC, abstractmethod


class IValidateCpfUseCase(ABC):
    """
    Interface para o caso de uso de validação de CPF.
    """
    @abstractmethod
    def execute(self, cpf_str: str) -> bool:
        """
        Executa a validação de um CPF fornecido como string.
        Retorna True se o CPF for válido, False caso contrário.
        """
        pass
