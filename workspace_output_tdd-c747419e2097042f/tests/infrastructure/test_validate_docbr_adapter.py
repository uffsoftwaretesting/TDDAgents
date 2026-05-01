import pytest

from src.infrastructure.adapters.validate_docbr_adapter import ValidateDocbrAdapter
from src.domain.models.cpf import CpfInvalidError


def test_is_valid_returns_true_for_valid_cpf():
    """
    Given a known valid CPF, ValidateDocbrAdapter.is_valid should return True.
    """
    adapter = ValidateDocbrAdapter()
    assert adapter.is_valid('111.444.777-35') is True


def test_is_valid_returns_false_for_invalid_cpf():
    """
    Given a known invalid CPF (wrong check digits), ValidateDocbrAdapter.is_valid should return False.
    """
    adapter = ValidateDocbrAdapter()
    assert adapter.is_valid('111.444.777-34') is False


def test_is_valid_raises_cpf_invalid_error_on_library_exception(monkeypatch):
    """
    If the underlying validate_docbr library raises, the adapter should map it to CpfInvalidError.
    """
    adapter = ValidateDocbrAdapter()
    # Force the internal validator to throw an unexpected exception
    def fake_validate(cpf):
        raise RuntimeError("library failure")
    monkeypatch.setattr(adapter._validator, 'validate', fake_validate)

    with pytest.raises(CpfInvalidError):
        adapter.is_valid('111.444.777-35')
