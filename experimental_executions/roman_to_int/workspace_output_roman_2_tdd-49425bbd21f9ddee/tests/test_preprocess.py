import pytest
from roman_converter.converter import preprocess_input

def test_preprocess_strips_spaces_and_uppercases():
    # Leading and trailing spaces and mixed case should be removed/normalized
    assert preprocess_input("  ixv  ") == "IXV"
    assert preprocess_input("\tMmI\n") == "MMI"


def test_preprocess_empty_string_after_strip_raises():
    # String containing only whitespace should raise ValueError
    with pytest.raises(ValueError) as excinfo:
        preprocess_input("   ")
    assert str(excinfo.value) == "Entrada vazia"

    # Completely empty string should also raise ValueError
    with pytest.raises(ValueError) as excinfo2:
        preprocess_input("")
    assert str(excinfo2.value) == "Entrada vazia"