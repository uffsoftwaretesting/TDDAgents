import pytest
from uuid import uuid4
from datetime import datetime, timezone

from src.usecases.retrieve_validation_usecase import RetrieveValidationUseCase
from src.domain.models import CPFValidation

@ pytest.mark.asyncio
async def test_retrieve_validation_found():
    # Arrange
    cpf = '12345678901'
    expected = CPFValidation(
        id=uuid4(),
        cpf=cpf,
        is_valid=True,
        created_at=datetime.now(timezone.utc)
    )
    class DummyRepo:
        async def get_by_cpf(self, input_cpf):
            # ensure the correct CPF is passed to the repository
            assert input_cpf == cpf
            return expected

    repo = DummyRepo()
    usecase = RetrieveValidationUseCase(repo)

    # Act
    result = await usecase.execute(cpf)

    # Assert
    assert result is expected, "UseCase should return the CPFValidation instance returned by repository"

@ pytest.mark.asyncio
async def test_retrieve_validation_not_found():
    # Arrange
    cpf = '00000000000'
    class DummyRepo:
        async def get_by_cpf(self, input_cpf):
            # ensure the correct CPF is passed to the repository
            assert input_cpf == cpf
            return None

    repo = DummyRepo()
    usecase = RetrieveValidationUseCase(repo)

    # Act
    result = await usecase.execute(cpf)

    # Assert
    assert result is None, "UseCase should return None when repository has no record for the given CPF"