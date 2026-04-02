import validate_docbr
from uuid import uuid4
from datetime import datetime, timezone

from src.domain.models import CPFValidation


class ValidateCPFUseCase:
    def __init__(self, repository):
        self.repository = repository

    async def execute(self, cpf: str) -> CPFValidation:
        # Validate CPF using validate-docbr
        is_valid = validate_docbr.CPF().validate(cpf)
        # Create domain entity with timestamp in UTC
        validation = CPFValidation(
            id=uuid4(),
            cpf=cpf,
            is_valid=is_valid,
            created_at=datetime.now(timezone.utc)
        )
        # Persist and return
        saved = await self.repository.save(validation)
        return saved