from typing import List
from src.domain.models import CPFValidation


class ListValidationsUseCase:
    def __init__(self, repository):
        self.repository = repository

    async def execute(self, page: int, size: int) -> List[CPFValidation]:
        """
        Execute the use case: retrieve a paginated list of CPFValidation entities.
        """
        return await self.repository.list_all(page, size)
