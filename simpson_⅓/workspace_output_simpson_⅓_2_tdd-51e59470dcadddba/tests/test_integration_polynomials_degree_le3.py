import pytest
from src.integration import integracao_simpson_1_3

@ pytest.mark.parametrize("c, interval, Ns", [
    (5, (0, 1), [2, 4, 6]),
    (7, (-1, 2), [2, 8, 10]),
])
def test_constant_function_exact(c, interval, Ns):
    """
    f(x) = c. Integral exata: c * (b - a)
    """
    a, b = interval
    expected = c * (b - a)
    for N in Ns:
        result = integracao_simpson_1_3(lambda x: c, a, b, N)
        assert result == pytest.approx(expected)

@ pytest.mark.parametrize("interval, Ns", [
    ((-1, 2), [2, 4, 6]),
    ((0, 5), [2, 10, 20]),
])
def test_linear_function_exact(interval, Ns):
    """
    f(x) = x. Integral exata: (b^2 - a^2) / 2
    """
    a, b = interval
    expected = (b**2 - a**2) / 2
    for N in Ns:
        result = integracao_simpson_1_3(lambda x: x, a, b, N)
        assert result == pytest.approx(expected)

@ pytest.mark.parametrize("interval, Ns", [
    ((-2, 3), [2, 4, 6]),
    ((1, 4), [2, 8, 12]),
])
def test_quadratic_function_exact(interval, Ns):
    """
    f(x) = x^2. Integral exata: (b^3 - a^3) / 3
    """
    a, b = interval
    expected = (b**3 - a**3) / 3
    for N in Ns:
        result = integracao_simpson_1_3(lambda x: x**2, a, b, N)
        assert result == pytest.approx(expected)

@ pytest.mark.parametrize("interval, Ns", [
    ((-1, 1), [2, 4, 8]),
    ((2, 5), [2, 6, 10]),
])
def test_cubic_function_exact(interval, Ns):
    """
    f(x) = x^3. Integral exata: (b^4 - a^4) / 4
    """
    a, b = interval
    expected = (b**4 - a**4) / 4
    for N in Ns:
        result = integracao_simpson_1_3(lambda x: x**3, a, b, N)
        assert result == pytest.approx(expected)
