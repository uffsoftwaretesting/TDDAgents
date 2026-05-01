import pytest

from roman_converter.validation import validate_characters
from roman_converter.constants import symbols

@pytest.mark.parametrize("roman", [
    "I",
    "V",
    "X",
    "L",
    "C",
    "D",
    "M",
    "IVXLCDM",
    "MCMIV",
    "XXII"
])
def test_validate_characters_accepts_valid_strings(roman):
    """
    All characters in the test string are valid Roman symbols, so no error should be raised.
    """
    # Should simply return None without exception
    result = validate_characters(roman, symbols)
    assert result is None

@pytest.mark.parametrize("roman, invalid_char", [
    ("A", "A"),
    ("IA", "A"),
    ("ABC", "A"),
    ("VXMB", "B"),
    ("ZIV", "Z"),
    ("XQY", "Q"),
])
def test_validate_characters_rejects_invalid_characters(roman, invalid_char):
    """
    Any invalid character should trigger a ValueError indicating the first invalid symbol.
    """
    with pytest.raises(ValueError) as excinfo:
        validate_characters(roman, symbols)
    assert str(excinfo.value) == f"Invalid character: {invalid_char}"