import pytest
from src.palindrome import _is_palindrome_core

@ pytest.mark.parametrize("s, expected", [
    # string vazia sempre é palíndrome
    ("", True),
    # único caractere
    ("x", True),
    # pares simples
    ("aa", True),
    ("ab", False),
    # ímpar simples
    ("aba", True),
    ("abc", False),
    # pares maiores
    ("abba", True),
    ("abca", False),
    # casos maiores
    ("racecar", True),
    ("abcdef", False),
])
def test_is_palindrome_core_various(s, expected):
    assert _is_palindrome_core(s) == expected
