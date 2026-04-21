import pytest
from src.integracao_simpson_1_3 import integracao_simpson_1_3

@ pytest.mark.parametrize("a, b", [
    (0, 0),
    (1.5, 1.5),
    (2, 2),
])
def test_a_equal_b_returns_zero(a, b):
    """
    Se a == b, a integral deve ser zero (0.0) sem erros.
    """
    result = integracao_simpson_1_3(lambda x: x, a, b, 2)
    assert result == 0.0

@ pytest.mark.parametrize("a, b", [
    (1, 0),
    (2.5, 2),
    (5, 4.9),
])
def test_a_greater_b_raises_value_error(a, b):
    """
    Se a > b, deve levantar ValueError informando que a deve ser <= b.
    """
    with pytest.raises(ValueError) as excinfo:
        integracao_simpson_1_3(lambda x: x, a, b, 2)
    assert str(excinfo.value) == "Limite inferior a deve ser menor ou igual ao limite superior b"