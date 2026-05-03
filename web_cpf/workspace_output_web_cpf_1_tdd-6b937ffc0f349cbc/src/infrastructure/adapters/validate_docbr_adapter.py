from validate_docbr import CPF as DocBRCPF
from src.infrastructure.ports.validate_cpf_repository import IValidateCpfRepository


class ValidateDocbrAdapter(IValidateCpfRepository):
    """
    Adapter for validate-docbr library to validate CPF.
    """
    def __init__(self):
        self._cpf = DocBRCPF()

    def validate(self, cpf_str: str) -> bool:
        """
        Delegates CPF validation to validate-docbr library.
        """
        return self._cpf.validate(cpf_str)
