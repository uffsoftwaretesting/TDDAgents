import pytest
from src.domain.entities.cpf import CPF


def test_clean_removes_mask_characters():
    assert CPF.clean("123.456.789-09") == "12345678909"
    assert CPF.clean("111.222.333-44") == "11122233344"
    # already clean
    assert CPF.clean("55566677788") == "55566677788"


def test_init_raises_value_error_for_incorrect_length():
    # too short
    with pytest.raises(ValueError) as exc:
        CPF("123.456.789-0")
    assert "CPF must have 11 digits" in str(exc.value)

    # too long
    long_cpf = "123.456.789-090"
    with pytest.raises(ValueError) as exc2:
        CPF(long_cpf)
    assert "CPF must have 11 digits" in str(exc2.value)


def test_rejects_sequence_of_same_digits():
    cpf = CPF("111.111.111-11")
    assert not cpf.is_valid()
    cpf2 = CPF("00000000000")
    assert not cpf2.is_valid()

@ pytest.mark.parametrize(
    "cpf_input", [
        # Known valid CPFs
        "529.982.247-25",
        "168.995.350-09",
        "11144477735",  # same without mask
    ]
)
def test_valid_cpf_digits(cpf_input):
    cpf = CPF(cpf_input)
    assert cpf.is_valid()

@ pytest.mark.parametrize(
    "cpf_input", [
        # Incorrect check digits
        "529.982.247-24",
        "168.995.350-08",
        "123.456.789-09",
    ]
)
def test_invalid_cpf_check_digits(cpf_input):
    cpf = CPF(cpf_input)
    assert not cpf.is_valid()

# New tests covering immutability, cleaning edges, and other uniform sequences

def test_raw_and_digits_are_stored_correctly():
    raw_input = "529.982.247-25"
    cpf = CPF(raw_input)
    assert cpf.raw == raw_input
    assert cpf.digits == "52998224725"


def test_raw_and_digits_are_immutable():
    cpf = CPF("529.982.247-25")
    with pytest.raises(AttributeError):
        cpf.raw = "000"
    with pytest.raises(AttributeError):
        cpf.digits = "11111111111"


def test_clean_removes_whitespace_and_parentheses():
    mixed = " (529) 982.247-25 "
    assert CPF.clean(mixed) == "52998224725"

@ pytest.mark.parametrize(
    "seq", [
        "222.222.222-22",
        "33333333333",
        "44444444444",
    ]
)
def test_rejects_other_uniform_digit_sequences(seq):
    assert not CPF(seq).is_valid()
