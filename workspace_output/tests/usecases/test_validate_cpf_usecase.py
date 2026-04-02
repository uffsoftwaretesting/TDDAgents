import pytest
import validate_docbr
from src.domain.models import CPFValidation
from src.usecases.validate_cpf_usecase import ValidateCPFUseCase

@ pytest.mark.asyncio
async def test_validate_cpf_valid(monkeypatch):
    # Arrange
    input_cpf = '12345678901'
    # Mock validate-docbr to return True
    called = {}
    def fake_validate(self, cpf):
        called['cpf'] = cpf
        return True
    monkeypatch.setattr(validate_docbr.CPF, 'validate', fake_validate)

    # Stub repository.save to capture the saved entity and return it
    saved = {}
    class DummyRepo:
        async def save(self, validation):
            saved['entity'] = validation
            return validation

    repo = DummyRepo()
    usecase = ValidateCPFUseCase(repo)

    # Act
    result = await usecase.execute(input_cpf)

    # Assert
    assert isinstance(result, CPFValidation)
    assert result.cpf == input_cpf
    assert result.is_valid is True
    # Ensure validate-docbr was called with the original CPF
    assert called['cpf'] == input_cpf
    # Ensure repository.save was called with the same entity
    assert saved['entity'] is result

@ pytest.mark.asyncio
async def test_validate_cpf_invalid(monkeypatch):
    # Arrange
    input_cpf = '00000000000'
    # Mock validate-docbr to return False
    monkeypatch.setattr(validate_docbr.CPF, 'validate', lambda self, cpf: False)

    # Stub repository.save to capture the saved entity
    saved = {}
    class DummyRepo:
        async def save(self, validation):
            saved['entity'] = validation
            return validation

    repo = DummyRepo()
    usecase = ValidateCPFUseCase(repo)

    # Act
    result = await usecase.execute(input_cpf)

    # Assert
    assert isinstance(result, CPFValidation)
    assert result.cpf == input_cpf
    assert result.is_valid is False
    # Ensure repository.save was called
    assert saved['entity'] is result

@ pytest.mark.asyncio
async def test_validate_cpf_formatted(monkeypatch):
    # Arrange
    input_cpf = '123.456.789-01'
    # Mock validate-docbr to return True
    monkeypatch.setattr(validate_docbr.CPF, 'validate', lambda self, cpf: True)

    # Stub repository.save to capture the saved entity
    saved = {}
    class DummyRepo:
        async def save(self, validation):
            saved['entity'] = validation
            return validation

    repo = DummyRepo()
    usecase = ValidateCPFUseCase(repo)

    # Act
    result = await usecase.execute(input_cpf)

    # Assert
    assert isinstance(result, CPFValidation)
    assert result.cpf == input_cpf
    assert result.is_valid is True
    # Ensure repository.save was called
    assert saved['entity'] is result
