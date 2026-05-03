import pytest
from src.domain.cpf import CPF, InvalidCPFFormatError, InvalidCPFSequenceError


def test_create_with_valid_digits_no_mask() -> None:
    cpf = CPF('52998224725')
    assert cpf.value == '52998224725'


def test_normalization_of_mask() -> None:
    cpf = CPF('529.982.247-25')
    assert cpf.value == '52998224725'


def test_invalid_format_length_not_11_digits() -> None:
    with pytest.raises(InvalidCPFFormatError):
        CPF('1234567890')


def test_invalid_format_invalid_characters() -> None:
    with pytest.raises(InvalidCPFFormatError):
        CPF('529.982.247-2a')


def test_invalid_sequence_all_digits_equal() -> None:
    with pytest.raises(InvalidCPFSequenceError):
        CPF('00000000000')
