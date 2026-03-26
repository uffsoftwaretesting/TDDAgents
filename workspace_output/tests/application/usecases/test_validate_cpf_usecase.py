import pytest
from datetime import datetime, timezone

from domain.entities.cpf import CPF
from domain.entities.cpf_validation import CPFValidation
from application.usecases.validate_cpf_usecase import ValidateCPFUseCase


class DummyRepo:
    """
    A simple stub repository for capturing save calls.
    """
    def __init__(self):
        self.saved = []

    async def save(self, cpf: str, valid: bool) -> CPFValidation:
        # Record the call
        self.saved.append((cpf, valid))
        # Return a dummy domain entity as if persisted
        return CPFValidation(
            id=1,
            cpf=CPF(cpf),
            valid=valid,
            timestamp=datetime.now(timezone.utc)
        )


@pytest.mark.asyncio
async def test_execute_calls_save_and_returns_validation_result_for_valid_cpf(monkeypatch):
    # Arrange: stub domain CPF.is_valid to True
    monkeypatch.setattr(CPF, 'is_valid', lambda self: True)
    repo = DummyRepo()
    usecase = ValidateCPFUseCase(repo)

    # Act
    result = await usecase.execute('12345678901')

    # Assert
    assert repo.saved == [('12345678901', True)]
    assert result.cpf == '12345678901'
    assert result.valid is True


@pytest.mark.asyncio
async def test_execute_calls_save_and_returns_validation_result_for_invalid_cpf(monkeypatch):
    # Arrange: stub domain CPF.is_valid to False
    monkeypatch.setattr(CPF, 'is_valid', lambda self: False)
    repo = DummyRepo()
    usecase = ValidateCPFUseCase(repo)

    # Act
    result = await usecase.execute('12345678901')

    # Assert
    assert repo.saved == [('12345678901', False)]
    assert result.cpf == '12345678901'
    assert result.valid is False


@pytest.mark.asyncio
async def test_execute_with_invalid_format_raises_value_error():
    # Arrange: invalid-format CPF should not even call repository
    repo = DummyRepo()
    usecase = ValidateCPFUseCase(repo)

    # Act & Assert
    with pytest.raises(ValueError):
        await usecase.execute('invalidcpf')
