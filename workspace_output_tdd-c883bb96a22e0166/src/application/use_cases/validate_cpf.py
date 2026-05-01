from src.application.ports.validate_cpf_usecase import ValidateCpfUseCase
from src.domain.ports.cpf_validator import CPFValidator


class ValidateCpfInteractor(ValidateCpfUseCase):
    """
    Implementation of ValidateCpfUseCase that delegates to a CPFValidator.
    """

    def __init__(self, validator: CPFValidator) -> None:
        self._validator = validator

    def execute(self, cpf: str) -> bool:
        """
        Validate the given CPF by delegating to the injected validator.
        """
        return self._validator.is_valid(cpf)
