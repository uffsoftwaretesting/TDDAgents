class CPFValidationRepository:
    """
    Interface for CPFValidation repository. Methods should be implemented by concrete classes.
    """
    
    async def save(self, validation):
        """Persist a CPFValidation entity."""
        raise NotImplementedError

    async def get_by_cpf(self, cpf):
        """Retrieve the most recent CPFValidation by CPF."""
        raise NotImplementedError

    async def list_all(self, page, size):
        """List paginated CPFValidation entities."""
        raise NotImplementedError
