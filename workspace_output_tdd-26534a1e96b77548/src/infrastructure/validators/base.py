class CPFValidator:
    """Interface for CPF validation adapters."""
    def is_valid(self, cpf: str) -> bool:
        """Return True if the given CPF string is valid, else False."""
        raise NotImplementedError("Subclasses must implement the is_valid method.")
