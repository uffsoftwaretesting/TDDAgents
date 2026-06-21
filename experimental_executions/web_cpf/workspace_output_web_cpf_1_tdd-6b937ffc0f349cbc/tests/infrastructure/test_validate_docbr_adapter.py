import pytest
import validate_docbr

from src.infrastructure.ports.validate_cpf_repository import IValidateCpfRepository
from src.infrastructure.adapters.validate_docbr_adapter import ValidateDocbrAdapter


def test_validate_docbr_adapter_implements_interface():
    adapter = ValidateDocbrAdapter()
    assert isinstance(adapter, IValidateCpfRepository)


@pytest.mark.parametrize("external_return, expected", [
    (True, True),
    (False, False),
])
def test_validate_returns_external_value(monkeypatch, external_return, expected):
    # Patch the external validate method to return a controlled value
    monkeypatch.setattr(validate_docbr.CPF, "validate", lambda self, cpf: external_return)
    adapter = ValidateDocbrAdapter()
    result = adapter.validate("12345678909")
    assert result is expected


def test_validate_propagates_exception(monkeypatch):
    # Define a dummy exception to simulate external failure
    class DummyException(Exception):
        pass

    def fake_validate(self, cpf):
        raise DummyException("External error")

    monkeypatch.setattr(validate_docbr.CPF, "validate", fake_validate)
    adapter = ValidateDocbrAdapter()
    with pytest.raises(DummyException):
        adapter.validate("12345678909")
