import pytest

from src.desconto import calcular_desconto


@pytest.mark.parametrize("preco", [0, 10, 99.99, 123.4567])
def test_percentual_zero_borda(preco):
    """
    Para percentual = 0, desconto deve ser 0.00 e preco_final deve ser o preco arredondado a 2 casas.
    """
    result = calcular_desconto(preco, 0)
    assert result["desconto"] == 0.00
    assert result["preco_final"] == round(preco, 2)


@pytest.mark.parametrize("preco", [0, 10, 99.99, 123.4567])
def test_percentual_cem_borda(preco):
    """
    Para percentual = 100, desconto deve ser o preco arredondado a 2 casas e preco_final = 0.00.
    """
    result = calcular_desconto(preco, 100)
    assert result["desconto"] == round(preco, 2)
    assert result["preco_final"] == 0.00
