import pytest

from roman_converter.validation import validate_non_empty


def test_validate_non_empty_empty_raises():
    """
    Empty input should raise ValueError with message "Input string is empty".
    """
    with pytest.raises(ValueError) as excinfo:
        validate_non_empty('')
    assert str(excinfo.value) == "Input string is empty"


def test_validate_non_empty_non_empty_passes():
    """
    Non-empty input should not raise and return None.
    """
    # Should simply return None without exception
    result = validate_non_empty('I')
    assert result is None
