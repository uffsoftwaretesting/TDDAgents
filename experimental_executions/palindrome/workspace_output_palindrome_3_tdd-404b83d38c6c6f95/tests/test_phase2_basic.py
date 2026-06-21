from src.palindrome import is_palindrome

import pytest

@ pytest.mark.parametrize("s", ["", "x", "Z", "9"])
def test_empty_and_single_character_are_palindromes(s):
    """
    Fase 2 – Casos Básicos:
    Strings vazias e de um único caractere devem retornar True.
    """
    assert is_palindrome(s) is True
