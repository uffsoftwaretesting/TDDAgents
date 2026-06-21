from .validators import ICPFValidator


class ValidateCPFUseCase:
    """
    Use case for validating a CPF string using an ICPFValidator.
    """
    def __init__(self, validator: ICPFValidator) -> None:
        self._validator = validator

    def execute(self, cpf: str) -> bool:
        """
        Execute the CPF validation and return True if valid, False otherwise.
        """
        return self._validator.is_valid(cpf)
