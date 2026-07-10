import pytest
from src.discount import calculate_discount


def test_price_not_numeric_string():
    with pytest.raises(TypeError) as excinfo:
        calculate_discount("100", 10)
    assert "price must be an int or float" in str(excinfo.value)


def test_price_not_numeric_none():
    with pytest.raises(TypeError) as excinfo:
        calculate_discount(None, 10)
    assert "price must be an int or float" in str(excinfo.value)


def test_discount_percentage_not_numeric_string():
    with pytest.raises(TypeError) as excinfo:
        calculate_discount(100, "10")
    assert "discount_percentage must be an int or float" in str(excinfo.value)


def test_discount_percentage_not_numeric_none():
    with pytest.raises(TypeError) as excinfo:
        calculate_discount(100, None)
    assert "discount_percentage must be an int or float" in str(excinfo.value)
