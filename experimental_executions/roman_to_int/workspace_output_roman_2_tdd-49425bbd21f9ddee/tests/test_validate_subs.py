import pytest
from roman_converter.converter import validate_subtraction_pairs

@ pytest.mark.parametrize("s", [
    "IV", "IX", "XL", "XC", "CD", "CM"
])
def test_validate_subs_accepts_valid_pairs(s):
    """
    Valid subtraction pairs should not raise any error.
    """
    # Should complete without exception
    validate_subtraction_pairs(s)

@ pytest.mark.parametrize("s, par", [
    ("IL", "IL"),
    ("IC", "IC"),
    ("VX", "VX"),
    ("XD", "XD"),
    ("LC", "LC"),
])
def test_validate_subs_rejects_invalid_pairs(s, par):
    """
    Invalid subtraction pairs should raise ValueError with correct message.
    """
    with pytest.raises(ValueError) as excinfo:
        validate_subtraction_pairs(s)
    assert str(excinfo.value) == f"Subtração inválida: {par}"
