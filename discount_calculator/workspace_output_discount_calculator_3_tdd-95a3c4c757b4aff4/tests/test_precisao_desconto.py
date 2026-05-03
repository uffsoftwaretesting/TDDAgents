import pytest

from src.desconto import calcular_desconto


def test_precisao_desconto_para_99_99_e_33_3333():
    """
    Verifica que, para preco=99.99 e percentual=33.3333,
    o desconto e preco_final sejam exatamente 33.33 e 66.66,
    arredondados a duas casas usando ROUND_HALF_UP.
    """
    result = calcular_desconto(99.99, 33.3333)
    assert result["desconto"] == 33.33
    assert result["preco_final"] == 66.66
