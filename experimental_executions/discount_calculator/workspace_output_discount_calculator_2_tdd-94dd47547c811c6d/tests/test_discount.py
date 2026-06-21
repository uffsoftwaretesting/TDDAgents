import pytest
from src.discount import calculate_discount

def test_standard_discount():
    result = calculate_discount(100, 10)
    assert result == {"discount_amount": 10.00, "final_price": 90.00}
