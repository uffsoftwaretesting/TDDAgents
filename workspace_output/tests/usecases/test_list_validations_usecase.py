import pytest
from uuid import uuid4
from datetime import datetime, timezone

from src.usecases.list_validations_usecase import ListValidationsUseCase
from src.domain.models import CPFValidation

@ pytest.mark.asyncio
async def test_list_validations_returns_items_for_valid_page():
    # Arrange
    page = 1
    size = 2
    # Prepare sample CPFValidation entities
    now = datetime.now(timezone.utc)
    validations = [
        CPFValidation(id=uuid4(), cpf='11111111111', is_valid=True, created_at=now),
        CPFValidation(id=uuid4(), cpf='22222222222', is_valid=False, created_at=now),
    ]
    class DummyRepo:
        async def list_all(self, input_page, input_size):
            # Ensure the use case passes correct pagination parameters
            assert input_page == page
            assert input_size == size
            return validations

    repo = DummyRepo()
    usecase = ListValidationsUseCase(repo)

    # Act
    result = await usecase.execute(page, size)

    # Assert
    assert result == validations, "UseCase should return the list of validations from repository"

@ pytest.mark.asyncio
async def test_list_validations_returns_empty_list_for_page_out_of_bounds():
    # Arrange
    page = 10
    size = 5
    class DummyRepo:
        async def list_all(self, input_page, input_size):
            # Ensure the use case passes correct pagination parameters
            assert input_page == page
            assert input_size == size
            return []

    repo = DummyRepo()
    usecase = ListValidationsUseCase(repo)

    # Act
    result = await usecase.execute(page, size)

    # Assert
    assert result == [], "UseCase should return an empty list when repository returns no items"
