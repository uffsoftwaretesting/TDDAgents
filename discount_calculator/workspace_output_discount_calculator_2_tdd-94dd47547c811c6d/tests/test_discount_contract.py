import pytest
from src.discount import calculate_discount

@ pytest.mark.parametrize(
    "price, discount_percentage", 
    [
        (100, 10),    # cenários típicos
        (0, 0),       # sem desconto
        (99.99, 12.5),# valores float
        (150.75, 100) # desconto total
    ]
)
def test_return_contract_structure_and_types(price, discount_percentage):
    result = calculate_discount(price, discount_percentage)
    # Deve ser um dicionário
    assert isinstance(result, dict)
    # Deve conter exatamente as duas chaves esperadas
    assert set(result.keys()) == {"discount_amount", "final_price"}
    # Cada valor deve ser float
    assert isinstance(result["discount_amount"], float)
    assert isinstance(result["final_price"], float)
