import pytest
from src.integracao import integracao_simpson_1_3


def test_import_integracao():
    # Verifica que a função existe e é chamável
    assert callable(integracao_simpson_1_3)


def test_f_not_callable_raises_type_error():
    with pytest.raises(TypeError) as exc:
        integracao_simpson_1_3(42, 0.0, 1.0, 2)
    assert str(exc.value) == "f must be callable"


@pytest.mark.parametrize("a,b", [
    ("0", 1.0),
    (0, None),
    ([], 1),
])
def test_a_b_not_numeric_raises_type_error(a, b):
    with pytest.raises(TypeError) as exc:
        integracao_simpson_1_3(lambda x: x, a, b, 2)
    assert str(exc.value) == "a and b must be numbers"


@pytest.mark.parametrize("N", [3.5, "2", None])
def test_N_not_integer_raises_value_error(N):
    with pytest.raises(ValueError) as exc:
        integracao_simpson_1_3(lambda x: x, 0.0, 1.0, N)
    assert str(exc.value) == "N must be an integer"


@pytest.mark.parametrize("N", [0, -2, 3, 5])
def test_N_not_positive_and_even_raises_value_error(N):
    with pytest.raises(ValueError) as exc:
        integracao_simpson_1_3(lambda x: x, 0.0, 1.0, N)
    assert str(exc.value) == "N must be positive and even"


def test_a_equals_b_returns_zero():
    # Intervalo de amplitude zero deve retornar 0.0
    def f(x):
        return x**2
    result = integracao_simpson_1_3(f, 2.0, 2.0, 2)
    assert result == 0.0


def test_a_greater_than_b_inverts_limits_and_sign():
    # ∫[1.0,0.0] 1 dx = -1.0
    def f(x):
        return 1.0
    result = integracao_simpson_1_3(f, 1.0, 0.0, 2)
    assert result == pytest.approx(-1.0)


def test_linear_function_integral_n2():
    # ∫[0.0,1.0] x dx = 1/2 = 0.5 usando Simpson composto com N=2
    def f(x):
        return x
    result = integracao_simpson_1_3(f, 0.0, 1.0, 2)
    assert result == pytest.approx(0.5)
