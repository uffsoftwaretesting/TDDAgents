import pytest
from src.adams_bashforth_2 import adams_bashforth_2

def test_euler_step_exception_propagates():
    """
    Se f levantar uma exceção no passo de arranque de Euler, deve propagar.
    """
    def f(t, y):
        raise KeyError("euler error")

    with pytest.raises(KeyError) as excinfo:
        adams_bashforth_2(f, 0.0, 1.0, 0.5, 0.5)
    assert "euler error" in str(excinfo.value)


def test_ab2_step_exception_propagates():
    """
    Se f levantar uma exceção durante o esquema AB2, deve propagar.
    """
    call_count = {"count": 0}

    def f(t, y):
        call_count["count"] += 1
        # Euler start (call 1) and AB2 f_curr (call 2) succeed
        if call_count["count"] <= 2:
            return 1.0
        # AB2 f_prev (call 3) raises
        raise ZeroDivisionError("ab2 error")

    # delta=1.0, h=0.5 -> n_steps=2 -> one AB2 iteration
    with pytest.raises(ZeroDivisionError) as excinfo:
        adams_bashforth_2(f, 0.0, 1.0, 1.0, 0.5)
    assert "ab2 error" in str(excinfo.value)


def test_too_many_steps_raises_runtime_error():
    """
    Se o número de passos (n_steps) for excessivo, deve lançar RuntimeError("too many steps").
    """
    # delta=1.0, h muito pequeno -> n_steps enorme
    with pytest.raises(RuntimeError) as excinfo:
        adams_bashforth_2(lambda t, y: 1.0, 0.0, 0.0, 1.0, 1e-9)
    assert "too many steps" in str(excinfo.value)
