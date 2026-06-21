import pytest
from src.discount import calculate_discount

def test_zero_discount_percentage():
    price = 150.75
    result = calculate_discount(price, 0)
    # Com 0% de desconto, não deve haver desconto e o preço final deve ser o preço original
    assert result == {"discount_amount": 0.0, "final_price": 150.75}

def test_full_discount_percentage():
    price = 150.75
    result = calculate_discount(price, 100)
    # Com 100% de desconto, o desconto deve ser o preço inteiro e preço final zero
    assert result == {"discount_amount": 150.75, "final_price": 0.0}