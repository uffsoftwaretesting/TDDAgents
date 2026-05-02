import pytest
from src.solve import solve

# Função dummy válida para uso em testes de validação de outros parâmetros
def dummy_f(t, y):
    return t + y


def test_f_not_callable_raises_TypeError():
    with pytest.raises(TypeError) as excinfo:
        solve(123, 0.0, 1.0, 0.0, 1)
    assert str(excinfo.value) == \
        "f must be a callable accepting (t: float, y: float) and returning float"


def test_f_wrong_signature_raises_TypeError():
    # Função que recebe apenas 1 argumento
    def f_one_arg(x):
        return x

    with pytest.raises(TypeError) as excinfo:
        solve(f_one_arg, 0.0, 1.0, 0.0, 1)
    assert str(excinfo.value) == \
        "f must be a callable accepting (t: float, y: float) and returning float"


@pytest.mark.parametrize(
    "t0, tf, y0",
    [
        ("0", 1.0, 0.0),
        (0.0, None, 0.0),
        (0.0, 1.0, []),
    ],
)
def test_t0_tf_y0_non_numeric_raises_TypeError(t0, tf, y0):
    with pytest.raises(TypeError) as excinfo:
        solve(dummy_f, t0, tf, y0, 1)
    assert str(excinfo.value) == \
        "t0, tf, y0 must be numeric (float or int)"


@pytest.mark.parametrize("n", [1.5, "10", None, []])
def test_n_non_integer_raises_TypeError(n):
    with pytest.raises(TypeError) as excinfo:
        solve(dummy_f, 0.0, 1.0, 0.0, n)
    assert str(excinfo.value) == "n must be an integer"


@pytest.mark.parametrize("n", [0, -1, -10])
def test_n_non_positive_raises_ValueError(n):
    with pytest.raises(ValueError) as excinfo:
        solve(dummy_f, 0.0, 1.0, 0.0, n)
    assert str(excinfo.value) == "n must be a positive integer"