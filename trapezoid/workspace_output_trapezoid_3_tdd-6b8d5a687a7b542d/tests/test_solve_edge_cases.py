import pytest
from src.solve import solve


def test_zero_length_interval_returns_zero():
    # Quando a == b, o comprimento do intervalo é zero e o resultado deve ser 0.0
    result = solve(lambda x: x**2 + 10, 5, 5, 1)
    assert result == pytest.approx(0.0)


def test_inverted_interval_returns_negative_consistent_value():
    # Quando a > b, o resultado deve ser o negativo do resultado com limites trocados
    f = lambda x: x + 2
    a, b, n = 3, 1, 5
    result_forward = solve(f, a, b, n)
    result_swapped = solve(f, b, a, n)
    assert result_forward == pytest.approx(-result_swapped)


def test_exception_propagation_from_function():
    # Se f levantar exceção em um ponto interno, solve deve propagar essa exceção
    def f(x):
        if x not in (0.0, 1.0):
            raise ValueError("f evaluation failed at interior point")
        return x

    with pytest.raises(ValueError) as excinfo:
        solve(f, 0, 1, 2)
    assert "f evaluation failed" in str(excinfo.value)
