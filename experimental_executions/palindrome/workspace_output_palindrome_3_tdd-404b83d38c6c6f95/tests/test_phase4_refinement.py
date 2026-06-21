import pytest
from src.palindrome import is_palindrome

@ pytest.mark.parametrize("s, expected", [
    ("12321", True),       # palíndromo numérico puro
    ("12345", False),      # não palíndromo numérico puro
])
def test_numeric_palindromes(s, expected):
    assert is_palindrome(s) is expected

@ pytest.mark.parametrize("s", [
    "   ",      # somente espaços -> string vazia -> True
    "\n\t",   # apenas caracteres de controle -> vazia -> True
])
def test_only_whitespace_and_control_characters(s):
    assert is_palindrome(s) is True

@ pytest.mark.parametrize("s", [
    "A_B-A",    # underscores/hífens removidos -> 'aba'
    "-X-X-"     # hífens removidos -> 'xx'
])
def test_underscore_and_hyphen_removed(s):
    assert is_palindrome(s) is True

def test_unicode_alphanumeric_palindrome():
    # Caracteres Unicode acentuados são considerados alnum e normalizados
    s = "Añá"
    # normalized = 'aña', reversed = 'aña'
    assert is_palindrome(s) is True

def test_return_type_is_bool():
    # Garantir que o retorno é sempre do tipo bool
    result = is_palindrome("abcba")
    assert isinstance(result, bool)
    result2 = is_palindrome("hello")
    assert isinstance(result2, bool)
