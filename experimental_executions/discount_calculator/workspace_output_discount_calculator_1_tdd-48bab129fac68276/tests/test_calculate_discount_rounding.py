import pytest
from calculate_discount import calculate_discount

@ pytest.mark.parametrize("price, discount_percent, expected", [
    # Cenário de arredondamento para baixo/para cima com ponto flutuante
    (0.333, 10, {'discount': 0.03, 'final_price': 0.30}),
    # Arredonda 5.055 para 5.06
    (10.11, 50, {'discount': 5.06, 'final_price': 5.06}),
    # Desconto simples, resultado não inteiro
    (19.99, 33.33, {'discount': 6.66, 'final_price': 13.33}),
    # Combinação de valores flutuantes complexos
    (45.67, 12.34, {'discount': 5.64, 'final_price': 40.03}),
])
def test_calculate_discount_rounding(price, discount_percent, expected):
    """
    Verifica o arredondamento correto a duas casas decimais em casos de precisão flutuante.
    """
    result = calculate_discount(price, discount_percent)
    assert result == expected
