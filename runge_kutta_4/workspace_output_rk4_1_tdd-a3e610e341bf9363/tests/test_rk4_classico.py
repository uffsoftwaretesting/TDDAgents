import pytest
import inspect
import math
from src.rk4_classico import rk4_classico


def test_rk4_signature():
    # Verifica que a assinatura da função está correta
    sig = inspect.signature(rk4_classico)
    params = list(sig.parameters.keys())
    assert params == ['f', 't0', 'y0', 't_final', 'h'], \
        f"Parâmetros esperados ['f', 't0', 'y0', 't_final', 'h'], mas encontrados {params}"
    # Verifica anotação de retorno
    assert sig.return_annotation == float, \
        f"Anotação de retorno esperada float, mas encontrada {sig.return_annotation}"


def test_rk4_constante_zero_derivative():
    # Se dy/dt = 0, y deve permanecer constante igual a y0
    f = lambda t, y: 0.0
    t0, y0, t_final, h = 0.0, 5.0, 10.0, 1.0
    result = rk4_classico(f, t0, y0, t_final, h)
    assert isinstance(result, float)
    assert result == pytest.approx(y0)


def test_rk4_linear_constant_derivative():
    # Se dy/dt = c, solução analítica y = y0 + c*(t_final - t0)
    c = 2.0
    f = lambda t, y: c
    t0, y0, t_final, h = 0.0, 1.5, 10.0, 2.5
    expected = y0 + c * (t_final - t0)
    result = rk4_classico(f, t0, y0, t_final, h)
    assert result == pytest.approx(expected, rel=1e-9)


def test_non_callable_f_raises_type_error():
    # f não é callable
    with pytest.raises(TypeError):
        rk4_classico(5, 0.0, 1.0, 1.0, 0.1)


def test_non_float_parameters_raise_type_error():
    # t0, y0, t_final ou h não são float
    f = lambda t, y: t + y
    with pytest.raises(TypeError):
        rk4_classico(f, "0.0", 1.0, 1.0, 0.1)
    with pytest.raises(TypeError):
        rk4_classico(f, 0.0, "1.0", 1.0, 0.1)
    with pytest.raises(TypeError):
        rk4_classico(f, 0.0, 1.0, "1.0", 0.1)
    with pytest.raises(TypeError):
        rk4_classico(f, 0.0, 1.0, 1.0, "0.1")


def test_h_non_positive_raises_value_error():
    # h <= 0 não é permitido
    f = lambda t, y: 0.0
    t0, y0, t_final = 0.0, 1.0, 1.0
    with pytest.raises(ValueError):
        rk4_classico(f, t0, y0, t_final, 0.0)
    with pytest.raises(ValueError):
        rk4_classico(f, t0, y0, t_final, -1.0)


def test_t_final_less_than_t0_raises_value_error():
    # t_final < t0 não é permitido
    f = lambda t, y: 0.0
    with pytest.raises(ValueError):
        rk4_classico(f, 1.0, 1.0, 0.0, 0.1)


def test_mesh_exact_division_steps_and_dt_final_zero():
    # Cenário exato: (t_final - t0)/h inteiro -> apenas passos de tamanho h
    class Recorder:
        def __init__(self):
            self.t_calls = []
        def __call__(self, t, y):
            self.t_calls.append(t)
            return 0.0

    recorder = Recorder()
    t0, y0, t_final, h = 0.0, 1.0, 1.0, 0.25
    result = rk4_classico(recorder, t0, y0, t_final, h)
    assert result == pytest.approx(y0)
    assert len(recorder.t_calls) == 16
    k1_times = [recorder.t_calls[i] for i in range(0, len(recorder.t_calls), 4)]
    diffs = [k1_times[i+1] - k1_times[i] for i in range(len(k1_times)-1)]
    assert diffs == [pytest.approx(h) for _ in diffs]


def test_mesh_with_remainder_steps_and_dt_final():
    # Cenário com resto: passo final < h deve ser aplicado
    class Recorder:
        def __init__(self):
            self.t_calls = []
        def __call__(self, t, y):
            self.t_calls.append(t)
            return 0.0

    recorder = Recorder()
    t0, y0 = 0.0, 2.0
    t_final, h = 1.1, 0.3
    result = rk4_classico(recorder, t0, y0, t_final, h)
    assert result == pytest.approx(y0)
    assert len(recorder.t_calls) == 16
    k1_times = [recorder.t_calls[i] for i in range(0, len(recorder.t_calls), 4)]
    dt_list = [k1_times[i+1] - k1_times[i] for i in range(len(k1_times)-1)]
    dt_list.append(t_final - k1_times[-1])
    expected_dt_final = t_final - (t0 + 3 * h)
    expected = [h, h, h, expected_dt_final]
    assert dt_list == [pytest.approx(val) for val in expected]


def test_rk4_exponential_solution_exact_steps():
    # Se dy/dt = y, solução analítica y = y0 * exp(t_final - t0), intervalo dividido exatamente
    f = lambda t, y: y
    t0, y0, t_final, h = 0.0, 2.0, 1.0, 0.25
    expected = y0 * math.exp(t_final - t0)
    result = rk4_classico(f, t0, y0, t_final, h)
    # tolerância rel=1e-4 reflete o erro esperado de RK4
    assert result == pytest.approx(expected, rel=1e-4)


def test_rk4_exponential_solution_with_remainder():
    # Se dy/dt = y, solução analítica y = y0 * exp(t_final - t0), intervalo com remainder
    f = lambda t, y: y
    t0, y0 = 0.0, 3.0
    t_final, h = 1.1, 0.3
    expected = y0 * math.exp(t_final - t0)
    result = rk4_classico(f, t0, y0, t_final, h)
    # tolerância rel=1e-4 reflete o erro esperado de RK4
    assert result == pytest.approx(expected, rel=1e-4)

# Novos testes para o sub-requisito de propagação de erros:

def test_f_raises_exception_propagates():
    # Se f lança exceção, deve propagar sem captura interna
    def f(t, y):
        raise RuntimeError("error in f")

    with pytest.raises(RuntimeError) as excinfo:
        rk4_classico(f, 0.0, 1.0, 1.0, 0.5)
    assert "error in f" in str(excinfo.value)


def test_f_returns_non_float_propagates_type_error():
    # Se f retorna tipo não-float, deve propagar o erro de operação (TypeError)
    def f(t, y):
        return "not a float"

    with pytest.raises(TypeError):
        rk4_classico(f, 0.0, 1.0, 1.0, 0.5)


def test_exponential_decay_solution_integration_end_to_end():
    """
    Integração end-to-end para ODE dy/dt = -y em [0,5].
    Solução analítica: y(t) = y0 * exp(-(t - t0)).
    """
    f = lambda t, y: -y
    t0, y0 = 0.0, 1.0
    t_final, h = 5.0, 0.5
    expected = y0 * math.exp(-(t_final - t0))
    result = rk4_classico(f, t0, y0, t_final, h)
    # tolerância rel=1e-2 para o erro global esperado de RK4 com h=0.5
    assert result == pytest.approx(expected, rel=1e-2)
