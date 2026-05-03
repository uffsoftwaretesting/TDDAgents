import pytest
from src.discount import calculate_discount

def test_float_price_and_percentage_calculation():
    """
    For price=99.99 and discount_percentage=12.5, 
    discount_amount should be round(99.99*0.125,2)=12.5,
    final_price should be round(99.99-12.5,2)=87.49
    """
    result = calculate_discount(99.99, 12.5)
    assert isinstance(result, dict)
    assert result["discount_amount"] == 12.5
    assert result["final_price"] == 87.49
