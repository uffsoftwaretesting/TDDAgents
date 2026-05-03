from src.application.ports.validate_cpf_use_case import IValidateCpfUseCase
from src.domain.cpf import CPF
from src.core.exceptions import InvalidCPFError
from src.infrastructure.adapters.validate_docbr_adapter import ValidateDocbrAdapter


class ValidateCpfUseCase(IValidateCpfUseCase):
    """
    Caso de uso de validação de CPF: instancia o domínio e delega a validação externa.
    """
    def __init__(self, repository=None):
        # If no repository is provided, use the default adapter
        if repository is None:
            repository = ValidateDocbrAdapter()
        self._repository = repository

    def execute(self, cpf_str: str) -> bool:
        try:
            cpf_entity = CPF(cpf_str)
        except InvalidCPFError:
            return False
        return self._repository.validate(cpf_entity.value)
