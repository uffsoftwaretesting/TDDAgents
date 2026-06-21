import pytest
from src.palindrome import is_palindrome

@ pytest.mark.parametrize("s, expected", [
    # Palíndromos numéricos
    ("12321", True),
    ("123321", True),
    # Não-palíndromo numérico
    ("123456", False),
    # Underscore é removido na normalização, deixando apenas 'a'
    ("_a_", True),
])
def test_is_palindrome_numeric_and_underscore(s, expected):
    assert is_palindrome(s) == expected
