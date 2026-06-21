from application.ports.icpf_validator import ICpfValidator
from validate_docbr import CPF


class CpfValidatorAdapter(ICpfValidator):
    """
    Adapter implementing ICpfValidator using the external validate_docbr.CPF library.
    """
    def __init__(self) -> None:
        # Instantiate the external CPF validator
        self._cpf = CPF()

    def validate(self, cpf: str) -> bool:
        # Normalize: remove any non-digit characters
        digits = ''.join(filter(str.isdigit, cpf))
        try:
            # Delegate to external library
            return self._cpf.validate(digits)
        except Exception:
            # On any error, treat as invalid
            return False