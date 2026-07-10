import pytest
from src.palindrome import is_palindrome

@pytest.mark.parametrize("s", [
    "hello",       # não palíndromo simples
    "Palindrome", # palavra comum
    "123ab321"    # números misturados
])
def test_non_palindromes_return_false(s):
    assert is_palindrome(s) is False

@pytest.mark.parametrize("s", [
    "!!!"          # apenas caracteres não alfanuméricos
])
def test_only_non_alphanumeric_returns_true(s):
    # após remover tudo, resulta em string vazia, que é palíndroma
    assert is_palindrome(s) is True
