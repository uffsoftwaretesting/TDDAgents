import pytest

from src.palindrome import is_palindrome

@pytest.mark.parametrize("s", [
    "A man, a plan, a canal: Panama",  # espaços, cases e pontuação
    "RaceCar",                          # case-insensitive
    "No 'x' in Nixon",                  # pontuação interna
    "",                                 # string vazia
    "Z"                                 # único caractere
])
def test_valid_palindromes(s):
    assert is_palindrome(s)

@pytest.mark.parametrize("s", [
    "hello",
    "Palindrome",
    "123ab321"
])
def test_non_palindromes(s):
    assert not is_palindrome(s)

@pytest.mark.parametrize("invalid", [None, 12345, ["a", "b"]])
def test_type_error(invalid):
    with pytest.raises(TypeError):
        is_palindrome(invalid)
