import pytest
import math
from src.solve import solve


def test_solve_exists():
    import src.solve as sm
    assert hasattr(sm, "solve"), "Function 'solve' should be defined in src.solve"


def test_solve_type_error_when_f_not_callable():
    # f must be callable
    with pytest.raises(TypeError) as excinfo:
        solve(123, 0, 1, 1)
    assert str(excinfo.value) == "f must be callable"


def test_solve_type_error_when_a_or_b_not_numeric():
    # a and b must be numeric
    f = lambda x: x
    with pytest.raises(TypeError) as excinfo:
        solve(f, "a", 1, 1)
    assert str(excinfo.value) == "a and b must be numeric"
    with pytest.raises(TypeError) as excinfo:
        solve(f, 0, "b", 1)
    assert str(excinfo.value) == "a and b must be numeric"


def test_solve_type_error_when_n_not_int():
    # n must be an integer
    f = lambda x: x
    with pytest.raises(TypeError) as excinfo:
        solve(f, 0, 1, 1.5)
    assert str(excinfo.value) == "n must be an integer"


def test_solve_value_error_when_n_not_positive():
    # n must be > 0
    f = lambda x: x
    with pytest.raises(ValueError) as excinfo:
        solve(f, 0, 1, 0)
    assert str(excinfo.value) == "n must be > 0"
    with pytest.raises(ValueError) as excinfo:
        solve(f, 0, 1, -5)
    assert str(excinfo.value) == "n must be > 0"


def test_solve_returns_correct_for_linear_stub_removed():
    # f(x)=x on [0,1] with n=10, exact integral = 0.5
    f = lambda x: x
    result = solve(f, 0, 1, 10)
    assert isinstance(result, float)
    assert result == pytest.approx(0.5)


def test_solve_returns_zero_when_a_equals_b():
    # b == a deve retornar 0.0
    f = lambda x: x**2
    result = solve(f, 5, 5, 10)
    assert result == 0.0


def test_solve_returns_zero_when_a_equals_b_for_floats():
    # b == a com limites float deve retornar 0.0
    f = lambda x: x**3
    result = solve(f, 2.5, 2.5, 5)
    assert isinstance(result, float)
    assert result == 0.0


def test_solve_int_and_float_equivalence():
    # passando a/b como int ou float deve produzir o mesmo resultado
    f = lambda x: x**2
    result_int = solve(f, 1, 3, 10)
    result_float = solve(f, 1.0, 3.0, 10)
    assert result_int == result_float


def test_solve_negative_result_when_b_less_than_a():
    # quando b < a o resultado deve ser negativo e corresponder ao valor exato para função constante
    f = lambda x: 1
    result = solve(f, 5, 3, 10)
    assert isinstance(result, float)
    assert result < 0.0
    # Para f(x)=1, integral de 5 a 3 deve ser -2.0
    assert result == pytest.approx(-2.0)

# New deterministic precision tests for known functions

def test_solve_linear_function_accuracy_single_interval():
    # f(x) = x on [0,1] with n=1, exact integral = 0.5
    f = lambda x: x
    result = solve(f, 0, 1, 1)
    assert result == pytest.approx(0.5)


def test_solve_quadratic_function_accuracy_convergence():
    # f(x) = x^2 on [0,2], exact integral = 8/3 ~ 2.6667
    f = lambda x: x**2
    exact = 8.0/3.0
    for n in [100, 1000]:
        result = solve(f, 0, 2, n)
        assert result == pytest.approx(exact, rel=1e-3)


def test_solve_sine_function_accuracy():
    # f(x) = sin(x) on [0, pi], exact integral = 2
    f = math.sin
    exact = 2.0
    for n in [100, 1000]:
        result = solve(f, 0, math.pi, n)
        assert result == pytest.approx(exact, rel=1e-3)
