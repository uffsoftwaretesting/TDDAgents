import pytest
from calculate_discount import calculate_discount


def test_zero_percent_discount():
    """
    Caso de borda: desconto de 0% em preço de 100
    Espera-se discount=0.00 e final_price=100.00
    """
    result = calculate_discount(100, 0)
    assert result == {'discount': 0.00, 'final_price': 100.00}


def test_full_percent_discount():
    """
    Caso de borda: desconto de 100% em preço de 100
    Espera-se discount=100.00 e final_price=0.00
    """
    result = calculate_discount(100, 100)
    assert result == {'discount': 100.00, 'final_price': 0.00}


def test_rounding_for_59_99_price_15_percent():
    """
    Caso de borda: preço=59.99 com 15% de desconto
    Cálculo bruto: 8.9985 → arredondado para 9.00
    final_price bruto: 50.9915 → arredondado para 50.99
    """
    result = calculate_discount(59.99, 15)
    assert result == {'discount': 9.00, 'final_price': 50.99}
