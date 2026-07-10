import pytest
from src.cep_formatter import format_cep

# Verifica que int com 8 dígitos é convertido para string e formatado corretamente
def test_format_cep_int_conversion_and_formatting():
    assert format_cep(87654321) == "87654-321"

# Verifica ValueError para strings com caracteres não numéricos (letras)
def test_format_cep_non_numeric_letters():
    with pytest.raises(ValueError) as exc:
        format_cep("abcdefgh")
    assert str(exc.value) == "CEP deve conter apenas dígitos."

# Verifica ValueError para strings que contêm hífen
def test_format_cep_non_numeric_hyphen():
    with pytest.raises(ValueError) as exc:
        format_cep("12345-678")
    assert str(exc.value) == "CEP deve conter apenas dígitos."

# Verifica ValueError para strings que contêm espaços
def test_format_cep_non_numeric_spaces():
    with pytest.raises(ValueError) as exc:
        format_cep(" 12345678 ")
    assert str(exc.value) == "CEP deve conter apenas dígitos."