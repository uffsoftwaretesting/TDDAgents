import pytest

from domain.cpf import Cpf, InvalidCpfFormat, InvalidCpfCheckDigits


def test_normalization_removes_mask():
    raw = "123.456.789-09"
    cpf = Cpf(raw)
    assert cpf.value == "12345678909"


@pytest.mark.parametrize("invalid_cpf", [
    "",           # empty string
    "123",        # too short
    "abcdefghijk",# non-numeric
    "1234567890a",# alphanumeric
    "123456789012",# too long
    "12.345.678/901" # wrong mask
])
def test_invalid_format_raises_invalid_format(invalid_cpf):
    with pytest.raises(InvalidCpfFormat):
        Cpf(invalid_cpf)


@pytest.mark.parametrize("invalid_cpf", [
    "12345678901", # wrong check digits
    "11111111111"  # all digits equal
])
def test_invalid_check_digits_raises_invalid_check_digits(invalid_cpf):
    with pytest.raises(InvalidCpfCheckDigits):
        Cpf(invalid_cpf)


@ pytest.mark.parametrize("raw, expected", [
    ("12345678909", "12345678909"),        # valid without mask
    ("529.982.247-25", "52998224725"),    # valid with mask
])
def test_valid_cpf_assigns_normalized_value(raw, expected):
    cpf = Cpf(raw)
    assert cpf.value == expected


@pytest.mark.parametrize("raw", [
    12345678909,  # integer
    None,         # NoneType
    12.345        # float
])
def test_non_string_input_raises_invalid_format(raw):
    with pytest.raises(InvalidCpfFormat):
        Cpf(raw)