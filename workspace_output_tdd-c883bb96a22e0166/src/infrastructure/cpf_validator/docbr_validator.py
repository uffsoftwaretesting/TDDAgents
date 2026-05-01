from src.domain.ports.cpf_validator import CPFValidator
import validate_docbr


class ValidateDocbrCpfValidator(CPFValidator):
    """
    Adapter that uses validate-docbr library to validate CPFs.
    """
    def __init__(self) -> None:
        self._validator = validate_docbr.CPF()

    def is_valid(self, cpf: str) -> bool:
        """
        Delegates to validate-docbr's CPF.validate method.
        """
        return self._validator.validate(cpf)
