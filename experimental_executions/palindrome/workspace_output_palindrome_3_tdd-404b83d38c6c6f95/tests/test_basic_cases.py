from src.palindrome import is_palindrome

def test_empty_string_is_palindrome():
    # String vazia deve ser considerada palíndromo
    assert is_palindrome("") is True

def test_single_character_is_palindrome():
    # String com único caractere deve ser considerada palíndromo
    assert is_palindrome("Z") is True
