import pytest
from src.cep_formatter import format_cep

@ pytest.mark.parametrize("invalid", [None, 12.345678, object()])
def test_format_cep_raises_type_error_para_tipo_invalido(invalid):
    with pytest.raises(TypeError) as exc:
        format_cep(invalid)
    assert str(exc.value) == "CEP deve ser uma string ou inteiro"


def test_format_cep_int_conversion_e_formatacao():
    # Verifica que um inteiro válido é convertido para string e formatado
    cep_int = 87654321
    resultado = format_cep(cep_int)
    assert isinstance(resultado, str)
    assert resultado == "87654-321"