import pytest
from src.palindrome import is_palindrome

@pytest.mark.parametrize("input_value", [None, 123, 12.3, [], {}, object()])
def test_is_palindrome_type_error(input_value):
    with pytest.raises(TypeError) as excinfo:
        is_palindrome(input_value)
    assert str(excinfo.value) == "Entrada deve ser uma string"


@pytest.mark.parametrize("s, expected", [
    # String vazia deve ser True
    ("", True),
    # Apenas caracteres removíveis (pontuação/espaços) normaliza para vazia -> True
    (" ,.!?\n\t", True),
    # Único caractere
    ("x", True),
    # Palíndromos com espaços e pontuação
    ("A man, a plan, a canal: Panama", True),
    ("No 'x' in Nixon", True),
    # Não-palíndromos
    ("hello", False),
    ("abc123", False),
])
def test_is_palindrome_end_to_end(s, expected):
    assert is_palindrome(s) == expected
