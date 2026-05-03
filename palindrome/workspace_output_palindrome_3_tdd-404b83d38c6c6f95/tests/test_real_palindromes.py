import pytest
from src.palindrome import is_palindrome

@ pytest.mark.parametrize("s", [
    "A man, a plan, a canal: Panama",  # espaços, pontuação e cases mistos
    "RaceCar",                          # case-insensitive
    "No 'x' in Nixon"                   # pontuação interna e spaces
])
def test_real_world_palindromes(s):
    """
    Casos reais de frases palíndromas após normalização:
    devem retornar True.
    """
    assert is_palindrome(s) is True
