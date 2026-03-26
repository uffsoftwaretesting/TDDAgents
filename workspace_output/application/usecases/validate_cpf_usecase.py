from domain.entities.cpf import CPF


class ValidationResultDTO:
    def __init__(self, cpf: str, valid: bool):
        self.cpf = cpf
        self.valid = valid


class ValidateCPFUseCase:
    def __init__(self, repo):
        self.repo = repo

    async def execute(self, cpf_str: str) -> ValidationResultDTO:
        # Validate CPF format; may raise ValueError
        cpf_entity = CPF(cpf_str)
        # Check validity
        is_valid = cpf_entity.is_valid()
        # Persist the validation
        await self.repo.save(cpf_str, is_valid)
        # Return the result DTO
        return ValidationResultDTO(cpf=cpf_str, valid=is_valid)
