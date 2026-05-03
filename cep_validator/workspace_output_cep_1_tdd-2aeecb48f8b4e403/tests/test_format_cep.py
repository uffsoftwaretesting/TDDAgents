import pytest
from src.cep_formatter import format_cep


def test_format_cep_retorna_string_formatada_para_str():
    assert format_cep("24350310") == "24350-310"


def test_format_cep_retorna_string_formatada_para_int():
    assert format_cep(12345678) == "12345-678"


def test_format_cep_preserva_zeros_a_esquerda():
    assert format_cep("00123456") == "00123-456"


def test_format_cep_retorna_zeros_totais():
    assert format_cep("00000000") == "00000-000"

@pytest.mark.parametrize("invalid", [None, 12.345678, object()])
def test_format_cep_raises_type_error_para_tipo_invalido(invalid):
    with pytest.raises(TypeError) as exc:
        format_cep(invalid)
    assert str(exc.value) == "CEP deve ser uma string ou inteiro"

@pytest.mark.parametrize("invalid", [
    "abcdefgh",    # todas letras
    " 12345678 ",  # espaços nas bordas
    "1234 678",    # espaço no meio
    "12a34567",    # letra no meio
    "12345-678",   # hífen dentro
    "1234567@",    # caractere especial
    "1234567\n",  # newline
    "\t1234567",  # tabulação
])
def test_format_cep_raises_value_error_para_conteudo_nao_numerico_str(invalid):
    with pytest.raises(ValueError) as exc:
        format_cep(invalid)
    assert str(exc.value) == "CEP deve conter apenas dígitos"

@pytest.mark.parametrize("invalid", [1234, 1234567, 123456789, 1234567890, 0])
def test_format_cep_raises_value_error_para_numeros_com_tamanho_invalido(invalid):
    with pytest.raises(ValueError) as exc:
        format_cep(invalid)
    assert str(exc.value) == "CEP deve ter exatamente 8 dígitos"

@pytest.mark.parametrize("invalid", ["1234", "1234567", "123456789", "1234567890"])
def test_format_cep_raises_value_error_para_str_com_tamanho_invalido(invalid):
    with pytest.raises(ValueError) as exc:
        format_cep(invalid)
    assert str(exc.value) == "CEP deve ter exatamente 8 dígitos"


def test_format_cep_raises_value_error_para_int_negativo():
    with pytest.raises(ValueError) as exc:
        format_cep(-12345678)
    # sinal de menos torna o conteúdo não numérico
    assert str(exc.value) == "CEP deve conter apenas dígitos"