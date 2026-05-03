from src.application.validators import ICPFValidator
from validate_docbr import CPF


class CPFValidatorAdapter(ICPFValidator):
    """Adapter implementing ICPFValidator using validate_docbr.CPF."""

    def __init__(self) -> None:
        # Instantiate external CPF validator
        self._cpf = CPF()

    def is_valid(self, cpf: str) -> bool:
        """
        Normalize the CPF string by removing any non-digit characters and use
        the external CPF.validate() method. Return False if any exception
        occurs.
        """
        normalized = ''.join(ch for ch in cpf if ch.isdigit())
        try:
            return self._cpf.validate(normalized)
        except Exception:
            return False
