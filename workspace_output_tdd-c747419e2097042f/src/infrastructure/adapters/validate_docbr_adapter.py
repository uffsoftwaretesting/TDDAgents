from validate_docbr import CPF as DocbrCPF

from src.infrastructure.cpf_validator_adapter_protocol import CPFValidatorAdapter
from src.domain.models.cpf import CpfInvalidError


class ValidateDocbrAdapter(CPFValidatorAdapter):
    """
    Adapter implementation using the validate-docbr library.
    """
    def __init__(self) -> None:
        self._validator = DocbrCPF()

    def is_valid(self, cpf: str) -> bool:
        """
        Return True if the given CPF is valid according to validate-docbr.
        Map any library exceptions to CpfInvalidError.
        """
        # Normalize by stripping mask characters
        normalized = cpf.replace('.', '').replace('-', '')
        try:
            return self._validator.validate(normalized)
        except Exception as e:
            raise CpfInvalidError(str(e))