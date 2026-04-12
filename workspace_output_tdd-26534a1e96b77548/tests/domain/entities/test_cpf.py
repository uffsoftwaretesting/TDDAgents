import pytest

from core.domain.entities.cpf import CPF
from core.domain.errors import DomainError


def test_empty_string_raises_domain_error():
    with pytest.raises(DomainError):
        CPF("")


def test_incorrect_length_raises_domain_error():
    # Apenas 10 dígitos após normalização
    with pytest.raises(DomainError):
        CPF("1234567890")


def test_repeated_digits_raises_domain_error():
    # Todos dígitos iguais após normalização
    with pytest.raises(DomainError):
        CPF("111.111.111-11")


def test_removal_of_non_digits_and_trim_success():
    raw = "  123.456.789-01  "
    cpf = CPF(raw)
    # raw deve ser trimado
    assert cpf.raw == raw.strip()
    # normalized deve ter apenas dígitos
    assert cpf.normalized == "12345678901"


def test_valid_cpf_without_mask_success():
    raw = "12345678901"
    cpf = CPF(raw)
    assert cpf.raw == raw
    assert cpf.normalized == raw
