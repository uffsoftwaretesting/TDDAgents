import pytest
from src.cep_formatter import format_cep

# 1. Inteiro maior que 8 dígitos
def test_format_cep_int_too_long():
    with pytest.raises(ValueError) as exc:
        format_cep(123456789)
    assert str(exc.value) == "CEP deve ter exatamente 8 dígitos."

# 2. Inteiro negativo deve falhar na validação de dígitos
def test_format_cep_negative_int():
    with pytest.raises(ValueError) as exc:
        format_cep(-12345678)
    assert str(exc.value) == "CEP deve conter apenas dígitos."

# 3. Zero como inteiro gera "00000-000"
def test_format_cep_zero_int():
    assert format_cep(0) == "00000-000"

# 4. Tipos tuple e set devem gerar TypeError
def test_format_cep_invalid_type_tuple():
    with pytest.raises(TypeError) as exc:
        format_cep((1,2,3,4,5,6,7,8))
    assert str(exc.value) == "Tipo inválido: esperado str ou int."

def test_format_cep_invalid_type_set():
    with pytest.raises(TypeError) as exc:
        format_cep({1,2,3,4,5,6,7,8})
    assert str(exc.value) == "Tipo inválido: esperado str ou int."