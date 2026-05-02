import pytest

from src.solver import _ab3_step


def test_ab3_step_zero_dt():
    """
    Se dt = 0, _ab3_step deve retornar o último y no histórico sem alteração.
    """
    hist = [
        (0.0, 1.0, 10.0),
        (0.1, 2.0, 20.0),
        (0.2, 3.0, 30.0),
    ]
    dt = 0.0
    expected = hist[2][1]
    result = _ab3_step(hist, dt)
    assert result == expected


def test_ab3_step_constant_derivative():
    """
    Para derivada constante C, AB3 deve ser exato: y_next = y_n + C * dt.
    """
    C = 4.0
    # f é constante, y evolui linearmente
    hist = [
        (0.0, 0.0, C),
        (1.0, 1.0 * C, C),
        (2.0, 2.0 * C, C),
    ]
    dt = 0.5
    expected = hist[2][1] + C * dt
    result = _ab3_step(hist, dt)
    assert pytest.approx(result, rel=1e-12) == expected


def test_ab3_step_variable_derivative():
    """
    Testa com derivadas diferentes: y_{n+1} = y_n + dt/12*(23 f_n - 16 f_{n-1} + 5 f_{n-2}).
    """
    hist = [
        (0.0, 0.0, 0.0),    # f_{n-2}
        (0.1, 0.1, 1.0),    # f_{n-1}
        (0.2, 0.2, 2.0),    # f_n
    ]
    dt = 0.1
    f_n2 = hist[0][2]
    f_n1 = hist[1][2]
    f_n  = hist[2][2]
    y_n  = hist[2][1]
    expected = y_n + dt/12 * (23 * f_n - 16 * f_n1 + 5 * f_n2)
    result = _ab3_step(hist, dt)
    assert pytest.approx(result, rel=1e-12) == expected
