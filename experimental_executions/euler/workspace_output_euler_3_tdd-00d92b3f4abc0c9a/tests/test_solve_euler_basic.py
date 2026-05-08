import pytest
from src.solve import solve


def test_euler_constant_function():
    """
    Testa Euler para f(t,y)=c constante. A solução deve ser y0 + c*(tf - t0).
    """
    cases = [
        (0.0, 5.0, 2.0, 3.0, 50),
        (1.5, 4.5, -2.0, 1.2, 30),
        (-2.0, 2.0, 5.0, -0.5, 40),
    ]
    for t0, tf, y0, c, n in cases:
        f = lambda t, y: c
        result = solve(f, t0, tf, y0, n)
        expected = y0 + c * (tf - t0)
        assert result == pytest.approx(expected, rel=1e-12), \
            f"Para f constante={c}, esperava {expected}, obteve {result}"


@pytest.mark.parametrize("t0, tf, y0, n", [
    (0.0, 1.0, 1.0, 10),
    (2.0, 3.0, 2.0, 20),
    (0.0, 2.0, 0.5, 100),
])
def test_euler_exponential_function(t0, tf, y0, n):
    """
    Testa Euler para f(t,y)=y. A solução numérica é y0 * (1 + h)**n.
    """
    f = lambda t, y: y
    result = solve(f, t0, tf, y0, n)
    h = (tf - t0) / n
    expected = y0 * ((1 + h) ** n)
    assert result == pytest.approx(expected, rel=1e-9), \
        f"Para f=y', esperava ~{expected}, obteve {result}"
