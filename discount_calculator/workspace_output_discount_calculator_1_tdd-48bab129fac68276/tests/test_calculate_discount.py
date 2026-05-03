import pytest
from calculate_discount import calculate_discount

def test_calculate_discount_basic():
    """
    Cenário básico: price=100, discount_percent=10
    Espera-se um dicionário com discount=10.00 e final_price=90.00.
    """
    result = calculate_discount(100, 10)
    assert result == {'discount': 10.00, 'final_price': 90.00}


def test_calculate_discount_price_invalid_type():
    """
    Deve levantar TypeError quando o preço não for int ou float.
    """
    with pytest.raises(TypeError):
        calculate_discount('100', 10)


def test_calculate_discount_discount_percent_invalid_type():
    """
    Deve levantar TypeError quando discount_percent não for int ou float.
    """
    with pytest.raises(TypeError):
        calculate_discount(100, None)

@pytest.mark.parametrize("price", [
    '100', None, [], {}, object(), (1, 2)
])
def test_calculate_discount_price_various_invalid_types(price):
    """
    Parametrização de vários tipos inválidos para price.
    """
    with pytest.raises(TypeError):
        calculate_discount(price, 10)

@pytest.mark.parametrize("discount_percent", [
    '10', None, [], {}, object(), (5,)
])
def test_calculate_discount_discount_percent_various_invalid_types(discount_percent):
    """
    Parametrização de vários tipos inválidos para discount_percent.
    """
    with pytest.raises(TypeError):
        calculate_discount(100, discount_percent)

@pytest.mark.parametrize("price, discount_percent", [
    (-1, 10),    # price negativo
    (100, -5),   # discount_percent negativo
    (100, 150)   # discount_percent acima de 100
])
def test_calculate_discount_invalid_domain_raises_value_error(price, discount_percent):
    """
    Deve levantar ValueError quando price < 0, discount_percent < 0 ou discount_percent > 100.
    """
    with pytest.raises(ValueError):
        calculate_discount(price, discount_percent)

@pytest.mark.parametrize("price, discount_percent, expected", [
    (100, 0, {'discount': 0.00, 'final_price': 100.00}),    # percent 0%
    (100, 100, {'discount': 100.00, 'final_price': 0.00}),  # percent 100%
    (0, 50, {'discount': 0.00, 'final_price': 0.00}),       # price zero
    (59.99, 15, {'discount': 9.00, 'final_price': 50.99})    # float & arredondamento
])
def test_calculate_discount_edge_valid_cases(price, discount_percent, expected):
    """
    Casos de borda válidos para domínio e arredondamento correto.
    """
    result = calculate_discount(price, discount_percent)
    assert result == expected
