import pytest
import math
from src.adams_bashforth_3 import adams_bashforth_3

# Interface existence and callability
def test_function_exists_and_callable():
    # Verifica se a função foi corretamente exportada e é chamável
    assert callable(adams_bashforth_3)


def test_interval_zero_returns_y0():
    # Se t_final == t0, retorna y0 sem chamar f
    assert adams_bashforth_3(lambda t, y: 1, 0.0, 5.0, 0.0, 0.1) == 5.0


def test_tfinal_equals_t0_no_function_call():
    # Se t_final == t0, deve retornar y0 sem invocar f
    def f(t, y):
        raise AssertionError("f foi chamado")
    result = adams_bashforth_3(f, 1.0, 3.14, 1.0, 0.5)
    assert result == 3.14


def test_invalid_step_h_zero():
    # h <= 0 deve lançar ValueError
    with pytest.raises(ValueError) as exc:
        adams_bashforth_3(lambda t, y: 1, 0.0, 5.0, 1.0, 0.0)
    assert "h deve ser maior que zero" in str(exc.value)


def test_invalid_step_h_negative():
    # h negativo deve lançar ValueError
    with pytest.raises(ValueError) as exc:
        adams_bashforth_3(lambda t, y: 1, 0.0, 5.0, 1.0, -0.1)
    assert "h deve ser maior que zero" in str(exc.value)


def test_tfinal_less_than_t0():
    # t_final < t0 deve lançar ValueError
    with pytest.raises(ValueError) as exc:
        adams_bashforth_3(lambda t, y: 1, 1.0, 5.0, 0.0, 0.1)
    assert "t_final deve ser maior ou igual a t0" in str(exc.value)


def test_f_not_callable():
    # f não chamável deve lançar TypeError
    with pytest.raises(TypeError) as exc:
        adams_bashforth_3(123, 0.0, 0.0, 1.0, 0.1)
    assert "f deve ser uma função chamável" in str(exc.value)


def test_t0_not_float():
    # t0 não é float deve lançar TypeError
    with pytest.raises(TypeError) as exc:
        adams_bashforth_3(lambda t, y: 1, "0.0", 0.0, 1.0, 0.1)
    assert "t0, y0, t_final e h devem ser floats" in str(exc.value)


def test_y0_not_float():
    # y0 não é float deve lançar TypeError
    with pytest.raises(TypeError) as exc:
        adams_bashforth_3(lambda t, y: 1, 0.0, "5.0", 1.0, 0.1)
    assert "t0, y0, t_final e h devem ser floats" in str(exc.value)


def test_tfinal_not_float():
    # t_final não é float deve lançar TypeError
    with pytest.raises(TypeError) as exc:
        adams_bashforth_3(lambda t, y: 1, 0.0, 0.0, "1.0", 0.1)
    assert "t0, y0, t_final e h devem ser floats" in str(exc.value)


def test_h_not_float():
    # h não é float deve lançar TypeError
    with pytest.raises(TypeError) as exc:
        adams_bashforth_3(lambda t, y: 1, 0.0, 0.0, 1.0, "0.1")
    assert "t0, y0, t_final e h devem ser floats" in str(exc.value)


def test_constant_derivative():
    # dy/dt = 0 deve manter y constante
    result = adams_bashforth_3(lambda t, y: 0.0, 0.0, 2.5, 1.0, 0.2)
    assert result == pytest.approx(2.5)


def test_insufficient_steps_error():
    # h maior que intervalo total gera insuficiência de passos para AB3
    with pytest.raises(ValueError) as exc:
        adams_bashforth_3(lambda t, y: 1.0, 0.0, 0.0, 0.5, 1.0)
    assert "passos insuficientes para AB3" in str(exc.value)

# New tests for the RK4 initialization phase
def test_rk4_two_steps_exponential():
    # For y'=y, two RK4 steps with t_final = t0 + 2*h should return y2 = y0 * factor^2
    t0 = 0.0
    y0 = 1.0
    h = 0.1
    t_final = t0 + 2 * h
    result = adams_bashforth_3(lambda t, y: y, t0, y0, t_final, h)
    # Analytical RK4 factor: 1 + h + h**2/2 + h**3/6 + h**4/24
    factor = 1 + h + h**2 / 2 + h**3 / 6 + h**4 / 24
    expected = y0 * factor * factor
    assert result == pytest.approx(expected, rel=1e-12)

def test_rk4_two_steps_truncated_constant():
    # First full RK4 step with constant f=3, second step truncated dt < h
    t0 = 0.0
    y0 = 2.0
    h = 0.6
    t_final = 1.0
    f = lambda t, y: 3.0
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    y1 = y0 + h * 3.0
    dt2 = t_final - (t0 + h)
    expected = y1 + dt2 * 3.0
    assert result == pytest.approx(expected)

def test_rk4_initial_steps_runtime_error():
    # If f raises during one of the RK4 substeps, wrap in RuntimeError
    def f(t, y):
        if abs(t - 0.05) < 1e-12:
            raise ValueError("fail")
        return y
    with pytest.raises(RuntimeError) as exc:
        adams_bashforth_3(f, 0.0, 1.0, 0.2, 0.1)
    assert "erro ao avaliar f: fail" in str(exc.value)

# New tests for uniform-step AB3

def test_ab3_uniform_constant():
    result = adams_bashforth_3(lambda t, y: 1.0, 0.0, 0.0, 1.0, 0.2)
    assert result == pytest.approx(1.0)

def test_ab3_uniform_constant_with_offset():
    t0 = 2.0
    y0 = 5.0
    h = 0.25
    t_final = 3.0
    result = adams_bashforth_3(lambda t, y: 1.0, t0, y0, t_final, h)
    expected = y0 + (t_final - t0)
    assert result == pytest.approx(expected)

def test_ab3_uniform_time_dependent_linear():
    t0 = 0.0
    y0 = 1.0
    h = 0.1
    t_final = 0.5
    f = lambda t, y: t
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    expected = y0 + (t_final**2 - t0**2) / 2
    assert result == pytest.approx(expected)

def test_ab3_truncated_last_step_constant():
    result = adams_bashforth_3(lambda t, y: 1.0, 0.0, 0.0, 1.0, 0.3)
    assert result == pytest.approx(1.0)

def test_insufficient_steps_nonzero_t0():
    t0 = 1.0
    y0 = 2.0
    t_final = 2.0
    h = 1.5
    with pytest.raises(ValueError) as exc:
        adams_bashforth_3(lambda t, y: 5.0, t0, y0, t_final, h)
    assert "passos insuficientes para AB3" in str(exc.value)

def test_ab3_runtime_error_on_value_error():
    t0 = 0.0
    y0 = 1.0
    h = 0.1
    t_final = 0.3
    def f(t, y):
        if abs(t - (t0 + 2*h)) < 1e-12:
            raise ValueError("value fail")
        return 1.0
    with pytest.raises(RuntimeError) as exc:
        adams_bashforth_3(f, t0, y0, t_final, h)
    assert "erro ao avaliar f: value fail" in str(exc.value)

def test_ab3_runtime_error_on_zero_division():
    # f causa ZeroDivisionError apenas durante AB3, não na inicialização RK4
    t0 = 0.0
    y0 = 2.0
    h = 0.1
    t_final = 0.3
    calls = {'n': 0}
    def f(t, y):
        calls['n'] += 1
        # initial f0 plus 8 RK4 subcalls = 9 calls; AB3 f_n is 10th call
        if calls['n'] > 9:
            return y / 0
        return 1.0
    with pytest.raises(RuntimeError) as exc:
        adams_bashforth_3(f, t0, y0, t_final, h)
    assert "erro ao avaliar f: division by zero" in str(exc.value)

# New tests for specific internal ZeroDivisionError conversion to ValueError
def test_rk4_internal_zero_division_to_value_error():
    def f(t, y):
        return y / 0  # ZeroDivisionError in RK4 k1
    with pytest.raises(ValueError) as exc:
        adams_bashforth_3(f, 0.0, 1.0, 0.1, 0.1)
    assert "erro numérico: divisão por zero" in str(exc.value)

def test_ab3_internal_zero_division_to_value_error():
    t0 = 0.0
    y0 = 1.0
    h = 0.1
    t_final = 0.3
    def f(t, y):
        if abs(t - (t0 + 2*h)) < 1e-12:
            return y / 0
        return 0.0
    with pytest.raises(ValueError) as exc:
        adams_bashforth_3(f, 0.0, 1.0, 0.3, 0.1)
    assert "erro numérico: divisão por zero" in str(exc.value)

# Final integration test for a non-trivial ODE
# y' = 2*t => analytical solution y = y0 + t^2 - t0^2
def test_integration_nontrivial_quadratic():
    t0 = 1.0
    y0 = 2.0
    t_final = 3.3
    h = 0.4
    f = lambda t, y: 2 * t
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    expected = y0 + (t_final**2 - t0**2)
    assert math.isclose(result, expected, rel_tol=1e-9, abs_tol=1e-12)

# Newly added tests to cover untested branches
def test_initial_truncated_rk4_exponential():
    # When t_final < t0 + 2*h, the second RK4 step is truncated
    t0 = 0.0
    y0 = 1.0
    h = 0.2
    t_final = 0.3
    f = lambda t, y: y
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    # first RK4 factor over h
    factor_h = 1 + h + h**2/2 + h**3/6 + h**4/24
    y1 = y0 * factor_h
    dt2 = t_final - (t0 + h)
    factor_dt2 = 1 + dt2 + dt2**2/2 + dt2**3/6 + dt2**4/24
    expected = y1 * factor_dt2
    assert result == pytest.approx(expected, rel=1e-12)

def test_initial_f_runtime_error():
    # If the very first f(t0, y0) raises, it's wrapped as RuntimeError
    def f(t, y):
        raise RuntimeError("init fail")
    with pytest.raises(RuntimeError) as exc:
        adams_bashforth_3(f, 0.0, 1.0, 0.1, 0.1)
    assert "erro ao avaliar f: init fail" in str(exc.value)
