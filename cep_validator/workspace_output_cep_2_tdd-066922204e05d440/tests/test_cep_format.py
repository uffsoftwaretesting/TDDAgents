import pytest
from src.cep_formatter import format_cep

def test_format_cep_valid_str():
    # Deve inserir hífen após os cinco primeiros dígitos
    assert format_cep("24350310") == "24350-310"

def test_format_cep_valid_int():
    # Deve aceitar inteiro e formatar corretamente
    assert format_cep(12345678) == "12345-678"