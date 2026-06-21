import pytest
from src.cep_formatter import format_cep

def test_format_cep_string_with_leading_zeros():
    # Entrada como string já com zeros à esquerda de total 8 dígitos
    assert format_cep("00000001") == "00000-001"

def test_format_cep_int_with_leading_zeros():
    # Entrada como inteiro deve ser convertida e preenchida com zeros à esquerda
    assert format_cep(1) == "00000-001"