import inspect
import pytest
from typing import Callable

from src.solver import adams_bashforth_3, ODEFunction, _rk4_step

def test_adams_bashforth_3_exists_and_callable():
    # A função deve estar definida e ser chamável
    assert callable(adams_bashforth_3), "adams_bashforth_3 deve ser chamável"


def test_adams_bashforth_3_signature():
    # Verifica assinatura de parâmetros
    sig = inspect.signature(adams_bashforth_3)
    params = list(sig.parameters.keys())
    assert params == ['f', 't0', 'y0', 't_final', 'h'], (
        f"Assinatura incorreta: esperava ['f','t0','y0','t_final','h'], obteve {params}"
    )
    # Verifica anotação de retorno
    return_ann = sig.return_annotation
    assert return_ann == float, (
        f"Anotação de retorno incorreta: esperava float, obteve {return_ann}"
    )


def test_adams_bashforth_3_annotations():
    # Verifica as anotações de tipo dos parâmetros e retorno
    sig = inspect.signature(adams_bashforth_3)
    params = sig.parameters
    # Parâmetro f deve ser ODEFunction
    assert params['f'].annotation == ODEFunction, (
        f"Anotação de 'f' incorreta: esperava ODEFunction, obteve {params['f'].annotation}"
    )
    # Parâmetros t0, y0, t_final, h devem ser float
    for name in ['t0', 'y0', 't_final', 'h']:
        assert params[name].annotation == float, (
            f"Anotação de '{name}' incorreta: esperava float, obteve {params[name].annotation}"
        )
    # Verifica novamente a anotação de retorno
    assert sig.return_annotation == float, (
        f"Anotação de retorno incorreta: esperava float, obteve {sig.return_annotation}"
    )


def test__rk4_step_constant_derivative():
    """
    Para f(t,y)=C, o passo RK4 deve ser exato: y_next = y + C*dt
    """
    C = 4.2
    def f(t, y):
        return C
    t0 = 0.5
    y0 = 1.3
    dt = 0.7
    expected = y0 + C * dt
    result = _rk4_step(f, t0, y0, dt)
    assert pytest.approx(result, rel=1e-12) == expected


def test__rk4_step_exponential():
    """
    Para f(t,y)=y, comparar com implementação local de um passo RK4
    """
    def f(t, y):
        return y
    t0 = 0.2
    y0 = 2.0
    dt = 0.1
    # Implementação local de um passo RK4
    def rk4_step_local(f, t, y, dt):
        k1 = f(t, y)
        k2 = f(t + dt/2, y + dt/2 * k1)
        k3 = f(t + dt/2, y + dt/2 * k2)
        k4 = f(t + dt, y + dt * k3)
        return y + dt/6 * (k1 + 2*k2 + 2*k3 + k4)
    expected = rk4_step_local(f, t0, y0, dt)
    result = _rk4_step(f, t0, y0, dt)
    assert pytest.approx(result, rel=1e-12) == expected


def test__rk4_step_zero_dt():
    """
    Se dt=0, _rk4_step deve retornar y inalterado
    """
    def f(t, y):
        return y + t
    t0 = 1.0
    y0 = -3.5
    dt = 0.0
    result = _rk4_step(f, t0, y0, dt)
    assert result == y0
