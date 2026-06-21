import pytest
from src.palindrome import _normalize

@pytest.mark.parametrize("input_str, expected", [
    # Uppercase should become lowercase
    ("AbCdE", "abcde"),
    # Remove spaces and punctuation
    ("A man, a plan: Panama!", "amanaplanpanama"),
    # Mixed alphanumeric and punctuation
    ("No 'x' in Nixon", "noxinnixon"),
    # Only removable characters yields empty string
    (" ,.!?\n\t", ""),
    # Alphanumeric and symbols
    ("123-456_789", "123456789"),
])
def test_normalize_various(input_str, expected):
    result = _normalize(input_str)
    assert result == expected


def test_normalize_empty_string():
    # Empty string remains empty
    assert _normalize("") == ""
