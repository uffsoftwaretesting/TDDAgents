import pytest
from src.domain.cpf import CPF
from src.core.exceptions import InvalidCPFError


def test_sanitization_removes_dots_and_dashes():
    # A known valid CPF with mask
    cpf = CPF("529.982.247-25")
    assert cpf.value == "52998224725"


@pytest.mark.parametrize(
    "invalid_cpf, expected_error_msg",
    [
        ("123.456.78a-09", "CPF must contain only digits after sanitization"),
        ("123.456.789-0", "CPF must have 11 digits"),
    ],
)
def test_invalid_format_raises_error(invalid_cpf, expected_error_msg):
    with pytest.raises(InvalidCPFError) as excinfo:
        CPF(invalid_cpf)
    assert expected_error_msg in str(excinfo.value)


def test_all_digits_equal_raises_error():
    with pytest.raises(InvalidCPFError) as excinfo:
        CPF("11111111111")
    assert "CPF cannot have all digits equal" in str(excinfo.value)


def test_incorrect_check_digits_raises_error():
    # Change last check digit of a known valid CPF to make it invalid
    with pytest.raises(InvalidCPFError) as excinfo:
        CPF("529.982.247-24")
    assert "Invalid CPF check digits" in str(excinfo.value)