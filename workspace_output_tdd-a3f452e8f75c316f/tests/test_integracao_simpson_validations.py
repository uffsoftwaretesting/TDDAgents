import pytest
from src.integracao_simpson_1_3 import integracao_simpson_1_3

@ pytest.mark.parametrize("invalid_N", [1.5, '2', None])
def test_N_not_int_raises_type_error(invalid_N):
    with pytest.raises(TypeError) as excinfo:
        integracao_simpson_1_3(lambda x: x, 0, 1, invalid_N)
    assert str(excinfo.value) == "N deve ser do tipo int"

@ pytest.mark.parametrize("invalid_N", [0, -2, 3, 5])
def test_N_non_positive_or_odd_raises_value_error(invalid_N):
    with pytest.raises(ValueError) as excinfo:
        integracao_simpson_1_3(lambda x: x, 0, 1, invalid_N)
    assert str(excinfo.value) == "N deve ser um inteiro par positivo"

@ pytest.mark.parametrize("a, b", [
    ('a', 1),
    (1, 'b'),
    (None, 1),
    (1, None),
    ([0], 1),
    (1, [2]),
])
def test_a_or_b_not_numeric_raises_type_error(a, b):
    with pytest.raises(TypeError) as excinfo:
        integracao_simpson_1_3(lambda x: x, a, b, 2)
    assert str(excinfo.value) == "Limites de integração devem ser números"