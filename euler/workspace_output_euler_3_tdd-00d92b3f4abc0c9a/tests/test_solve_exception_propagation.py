import pytest
from src.solve import solve

def test_exception_propagation_at_step_k():
    """
    Verifica que uma exceção levantada por f(t, y) no passo k é propagada sem ser suprimida.
    """
    exception_step = 3
    calls = {'count': 0}

    def f(t, y):
        # Conta quantas vezes f foi chamado e lança na iteração exception_step
        calls['count'] += 1
        if calls['count'] == exception_step:
            raise RuntimeError(f"error at step {exception_step}")
        return 1.0

    t0 = 0.0
    tf = 1.0
    y0 = 0.0
    n = 5

    # Deve propagar a RuntimeError sem capturar
    with pytest.raises(RuntimeError) as excinfo:
        solve(f, t0, tf, y0, n)
    assert str(excinfo.value) == f"error at step {exception_step}"