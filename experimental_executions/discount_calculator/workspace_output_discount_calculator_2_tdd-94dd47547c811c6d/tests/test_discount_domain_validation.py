import pytest
from src.discount import calculate_discount

def test_price_negative_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        calculate_discount(-1, 10)
    assert "price must be non-negative" in str(excinfo.value)


def test_discount_percentage_less_than_zero_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        calculate_discount(100, -5)
    assert "discount_percentage must be between 0 and 100" in str(excinfo.value)


def test_discount_percentage_greater_than_hundred_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        calculate_discount(100, 150)
    assert "discount_percentage must be between 0 and 100" in str(excinfo.value)