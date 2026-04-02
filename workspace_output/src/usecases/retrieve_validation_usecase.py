from typing import Optional
from src.domain.models import CPFValidation

class RetrieveValidationUseCase:
    def __init__(self, repository):
        self.repository = repository

    async def execute(self, cpf: str) -> Optional[CPFValidation]:
        return await self.repository.get_by_cpf(cpf)
