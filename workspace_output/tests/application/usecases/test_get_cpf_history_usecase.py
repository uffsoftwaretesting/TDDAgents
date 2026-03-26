import pytest
from datetime import datetime, timezone

from domain.entities.cpf import CPF
from domain.entities.cpf_validation import CPFValidation
from application.usecases.get_cpf_history_usecase import GetCPFHistoryUseCase, CPFHistoryDTO


class DummyRepo:
    """
    A stub repository for testing GetCPFHistoryUseCase.
    """
    def __init__(self, validations):
        self.validations = validations
        self.cpf_arg = None

    async def get_by_cpf(self, cpf: str) -> list[CPFValidation]:
        # Capture the input and return the predefined list
        self.cpf_arg = cpf
        return self.validations


@pytest.mark.asyncio
async def test_execute_returns_empty_results_when_no_validations():
    # Arrange
    cpf_input = "12345678901"
    repo = DummyRepo(validations=[])
    usecase = GetCPFHistoryUseCase(repo)

    # Act
    result = await usecase.execute(cpf_input)

    # Assert
    # Repository was called with the correct CPF
    assert repo.cpf_arg == cpf_input
    # Result is the expected DTO
    assert isinstance(result, CPFHistoryDTO)
    assert result.cpf == cpf_input
    # No history results
    assert isinstance(result.results, list)
    assert result.results == []


@pytest.mark.asyncio
async def test_execute_returns_sorted_results_by_timestamp():
    # Arrange
    cpf_value = "12345678901"
    cpf_entity = CPF(cpf_value)
    # Create timestamps out of order
    t1 = datetime(2020, 1, 1, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2020, 1, 2, 10, 0, tzinfo=timezone.utc)
    t3 = datetime(2020, 1, 3, 10, 0, tzinfo=timezone.utc)
    # Build domain CPFValidation objects in random order
    v1 = CPFValidation(cpf=cpf_entity, valid=False, timestamp=t2, id=2)
    v2 = CPFValidation(cpf=cpf_entity, valid=True, timestamp=t3, id=3)
    v3 = CPFValidation(cpf=cpf_entity, valid=True, timestamp=t1, id=1)
    repo = DummyRepo(validations=[v1, v2, v3])
    usecase = GetCPFHistoryUseCase(repo)

    # Act
    result = await usecase.execute(cpf_value)

    # Assert
    assert result.cpf == cpf_value
    # Results should be sorted by timestamp ascending
    timestamps = [entry.timestamp for entry in result.results]
    valids = [entry.valid for entry in result.results]
    assert timestamps == [t1, t2, t3]
    assert valids == [True, False, True]
