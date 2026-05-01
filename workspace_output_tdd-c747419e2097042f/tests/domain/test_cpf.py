import pytest

from src.domain.models.cpf import CPF, CpfInvalidError


def test_remove_mask_removes_dots_and_dashes():
    c = object.__new__(CPF)
    c.raw_value = '123.456.789-09'
    result = c._remove_mask()
    assert result == '12345678909'


def test_validate_length_too_short():
    c = object.__new__(CPF)
    c.normalized_value = '123456789'  # 9 digits only
    with pytest.raises(CpfInvalidError):
        c._validate_length()


def test_validate_length_too_long():
    c = object.__new__(CPF)
    c.normalized_value = '123456789012'  # 12 digits
    with pytest.raises(CpfInvalidError):
        c._validate_length()


def test_validate_sequence_identical_digits():
    c = object.__new__(CPF)
    c.normalized_value = '11111111111'
    with pytest.raises(CpfInvalidError):
        c._validate_sequence()


def test_validate_check_digits_invalid():
    c = object.__new__(CPF)
    c.normalized_value = '11144477734'  # wrong last digit
    with pytest.raises(CpfInvalidError):
        c._validate_check_digits()


def test_validate_check_digits_valid():
    c = object.__new__(CPF)
    c.normalized_value = '11144477735'  # valid sample
    # Não deve lançar
    c._validate_check_digits()


def test_integration_valid_cpf_with_mask():
    cpf_str = '111.444.777-35'
    c = CPF(cpf_str)
    assert c.normalized_value == '11144477735'


def test_integration_valid_cpf_without_mask():
    cpf_str = '11144477735'
    c = CPF(cpf_str)
    assert c.normalized_value == '11144477735'


def test_integration_invalid_characters_in_cpf():
    # Letras devem ser removidas, mas dígitos verificadores estão incorretos
    with pytest.raises(CpfInvalidError):
        CPF('123a456b789-09')


def test_integration_empty_cpf_raises_error():
    with pytest.raises(CpfInvalidError):
        CPF('')


def test_raw_value_immutable():
    cpf_str = '111.444.777-35'
    cpf = CPF(cpf_str)
    with pytest.raises(AttributeError):
        cpf.raw_value = '123.456.789-09'


def test_valid_cpf_properties():
    cpf_str = '111.444.777-35'
    cpf = CPF(cpf_str)
    assert cpf.raw_value == cpf_str
    assert cpf.normalized_value == '11144477735'