import pytest
from roman_converter.converter import validate_characters

@ pytest.mark.parametrize("s", [
    "I", "V", "X", "L", "C", "D", "M",
    "IVXLCDM", "MDCLXVI", "MMMCMXCIX"
])
def test_validate_chars_accepts_valid_characters(s):
    # Should not raise any error for valid Roman characters
    validate_characters(s)

@ pytest.mark.parametrize("invalid_s, invalid_char", [
    ("A", "A"),
    ("Z", "Z"),
    ("1", "1"),
    ("IXY", "Y"),
    ("VI A", " "),
])
def test_validate_chars_raises_for_invalid_characters(invalid_s, invalid_char):
    # The function should raise ValueError for the first invalid character
    with pytest.raises(ValueError) as excinfo:
        validate_characters(invalid_s)
    assert str(excinfo.value) == f"Caractere inválido: {invalid_char}"