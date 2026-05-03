import pytest
from src.palindrome import is_palindrome

@pytest.mark.parametrize("invalid", [
    None,
    12345,
    123.45,
    ["a", "b"],
    ("a",),
    {"a": "b"},
    {1, 2},
    b"bytes",
    False,
    True
])
def test_type_error_message(invalid):
    """
    Garante que chamar is_palindrome com tipos inválidos lança TypeError
    com a mensagem exata esperada.
    """
    with pytest.raises(TypeError) as excinfo:
        is_palindrome(invalid)
    assert str(excinfo.value) == "O argumento 's' deve ser do tipo str"