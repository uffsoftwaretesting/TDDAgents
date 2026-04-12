import pytest
import types

from core.domain.errors import DomainError
from application.dto.validation_result import ValidationResult
from application.use_cases.validate_cpf import ValidateCPFUseCase


def test_valid_cpf_returns_valid_true_and_formatted(monkeypatch):
    # Force the validator to return True
    monkeypatch.setattr(
        'infrastructure.validators.validate_docbr.LibraryCPFValidator.is_valid',
        lambda self, cpf: True
    )
    use_case = ValidateCPFUseCase()
    raw = '12345678909'
    result = use_case.execute(raw)
    assert isinstance(result, ValidationResult)
    assert result.cpf_original == raw
    assert result.valid is True
    assert result.cpf_formatado == '123.456.789-09'


def test_invalid_cpf_domain_error_returns_valid_false_and_empty_format():
    # Empty string triggers domain error in CPF entity
    use_case = ValidateCPFUseCase()
    result = use_case.execute('')
    assert isinstance(result, ValidationResult)
    # raw value is trimmed inside domain
    assert result.cpf_original == ''
    assert result.valid is False
    assert result.cpf_formatado == ''


def test_invalid_cpf_failed_digit_validation_returns_valid_false_and_formatted(monkeypatch):
    # Force the validator to return False
    monkeypatch.setattr(
        'infrastructure.validators.validate_docbr.LibraryCPFValidator.is_valid',
        lambda self, cpf: False
    )
    use_case = ValidateCPFUseCase()
    raw = '12345678909'
    result = use_case.execute(raw)
    assert isinstance(result, ValidationResult)
    assert result.cpf_original == raw
    assert result.valid is False
    # Should still format the normalized CPF
    assert result.cpf_formatado == '123.456.789-09'


def test_input_with_mask_and_whitespace_formats_correctly(monkeypatch):
    # Force the validator to return True
    monkeypatch.setattr(
        'infrastructure.validators.validate_docbr.LibraryCPFValidator.is_valid',
        lambda self, cpf: True
    )
    use_case = ValidateCPFUseCase()
    raw_input = ' 123.456.789-09 '
    result = use_case.execute(raw_input)
    # CPF.raw inside entity is stripped
    assert result.cpf_original == raw_input.strip()
    assert result.valid is True
    assert result.cpf_formatado == '123.456.789-09'
