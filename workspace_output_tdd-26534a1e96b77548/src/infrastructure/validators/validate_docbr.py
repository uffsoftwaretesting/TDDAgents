from .base import CPFValidator

class LibraryCPFValidator(CPFValidator):
    """Adapter for the validate-docbr CPF validation library."""
    def is_valid(self, cpf: str) -> bool:
        # Import the external library only at runtime for easier test stubbing
        import validate_docbr
        validator = validate_docbr.CPF()
        return validator.validate(cpf)
