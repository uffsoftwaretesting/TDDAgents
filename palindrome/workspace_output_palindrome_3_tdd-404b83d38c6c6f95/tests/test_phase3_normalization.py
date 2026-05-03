import pytest
from src.palindrome import is_palindrome

@ pytest.mark.parametrize("s, expected", [
    ("Madam, I'm Adam", True),         # pontuação e apóstrofo são removidos
    ("No lemon, no melon!", True),     # espaços e pontuação são removidos
    ("123@321", True),                 # símbolos internos são removidos
    ("!!@@!!", True),                  # apenas não alfanuméricos -> string vazia -> palíndromo
    ("1a2", False),                    # normalized '1a2' != '2a1'
    ("1A2a1", True),                   # normalização para lower -> '1a2a1'
])
def test_normalization_and_palindrome_logic(s, expected):
    assert is_palindrome(s) is expected
