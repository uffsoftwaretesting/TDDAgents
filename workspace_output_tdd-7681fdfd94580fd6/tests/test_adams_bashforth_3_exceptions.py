import pytest

from src.adams_bashforth_3 import adams_bashforth_3


def test_exception_in_euler_first_step():
    # Para n_steps == 1, a primeira chamada a f (no Euler) deve propagar a exceção
    def f(t, y):
        raise ValueError("f error first step")

    t0 = 0.0
    y0 = 1.0
    h = 0.1
    t_final = t0 + 1 * h
    with pytest.raises(ValueError, match="f error first step"):
        adams_bashforth_3(f, t0, y0, t_final, h)


def test_exception_in_euler_second_step():
    # Para n_steps == 2, a segunda chamada a f (segundo passo de Euler) deve propagar RuntimeError
    calls = []

    def f(t, y):
        # Conta chamadas; na segunda, lança
        calls.append((t, y))
        if len(calls) == 2:
            raise RuntimeError("error in second step")
        return 1.0

    t0 = 0.0
    y0 = 2.0
    h = 0.5
    t_final = t0 + 2 * h
    with pytest.raises(RuntimeError, match="error in second step"):
        adams_bashforth_3(f, t0, y0, t_final, h)


def test_exception_in_ab3_step():
    # Para n_steps == 3, a chamada a f no loop AB3 (terceira chamada) deve propagar ValueError
    calls = []

    def f(t, y):
        calls.append((t, y))
        # Na terceira chamada, que corresponde a f(t2, y2) dentro do AB3, lança
        if len(calls) == 3:
            raise ValueError("error in AB3 step")
        return 2.0

    t0 = 0.0
    y0 = 1.0
    h = 0.1
    t_final = t0 + 3 * h
    with pytest.raises(ValueError, match="error in AB3 step"):
        adams_bashforth_3(f, t0, y0, t_final, h)


def test_exception_in_ab3_loop_first_iteration():
    # Para n_steps == 4, a chamada a f no primeiro passo do loop AB3 deve propagar ZeroDivisionError
    calls = []

    def f(t, y):
        calls.append((t, y))
        # No 7º chamada ao f, que é a primeira chamada do loop AB3, lança
        if len(calls) == 7:
            raise ZeroDivisionError("error in AB3 loop")
        return 1.0

    t0 = 0.0
    y0 = 1.0
    h = 0.1
    t_final = t0 + 4 * h
    with pytest.raises(ZeroDivisionError, match="error in AB3 loop"):
        adams_bashforth_3(f, t0, y0, t_final, h)
