import pytest
from src.solve import solve


def test_f_not_callable_raises_type_error():
    # f não é callable ➞ TypeError
    with pytest.raises(TypeError):
        solve(123, 0, 1, 1)


def test_a_not_numeric_raises_type_error():
    # a não numérico ➞ TypeError
    with pytest.raises(TypeError):
        solve(lambda x: x, 'a', 1, 1)


def test_b_not_numeric_raises_type_error():
    # b não numérico ➞ TypeError
    with pytest.raises(TypeError):
        solve(lambda x: x, 0, None, 1)


def test_n_zero_or_negative_raises_value_error():
    # n = 0 ➞ ValueError
    with pytest.raises(ValueError):
        solve(lambda x: x, 0, 1, 0)
    # n negativo ➞ ValueError
    with pytest.raises(ValueError):
        solve(lambda x: x, 0, 1, -5)


def test_n_non_int_type_raises_value_error():
    # n não é int (float) ➞ ValueError
    with pytest.raises(ValueError):
        solve(lambda x: x, 0, 1, 2.5)
    # n não é int (string) ➞ ValueError
    with pytest.raises(ValueError):
        solve(lambda x: x, 0, 1, '10')


def test_a_invalid_types_additional():
    # a com tipos inválidos ➞ TypeError
    invalid_as = [[], {}, (1,), object(), 1+2j]
    for invalid in invalid_as:
        with pytest.raises(TypeError):
            solve(lambda x: x, invalid, 1, 1)


def test_b_invalid_types_additional():
    # b com tipos inválidos ➞ TypeError
    invalid_bs = [[], {}, (1,), object(), 1+2j]
    for invalid in invalid_bs:
        with pytest.raises(TypeError):
            solve(lambda x: x, 0, invalid, 1)


def test_n_invalid_types_additional():
    # n com tipos inválidos ➞ ValueError
    invalid_ns = [None, [], {}, 3.0, '5', 1+0j]
    for invalid in invalid_ns:
        with pytest.raises(ValueError):
            solve(lambda x: x, 0, 1, invalid)
