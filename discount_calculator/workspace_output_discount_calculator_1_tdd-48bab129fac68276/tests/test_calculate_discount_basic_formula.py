import pytest
from calculate_discount import calculate_discount

@pytest.mark.parametrize("price, discount_percent", [
    (100, 10),    # desconto de 10% em 100 => 10 e 90
    (200, 25),    # desconto de 25% em 200 => 50 e 150
    (99, 10),     # desconto de 10% em 99 => 9.9 e 89.1
    (50, 20),     # desconto de 20% em 50 => 10 e 40
])
def test_basic_discount_calculation(price, discount_percent):
    """
    Valida cálculo básico de discount e final_price sem introduzir efeitos de arredondamento complexos.
    """
    result = calculate_discount(price, discount_percent)
    expected_discount = price * (discount_percent / 100)
    expected_final_price = price - expected_discount
    # Como estamos usando casos simples, o round(..., 2) não altera o valor
    assert result['discount'] == round(expected_discount, 2)
    assert result['final_price'] == round(expected_final_price, 2)
