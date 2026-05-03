import pytest
from src.solve import solve


def test_f_not_callable_raises_type_error():
    """
    Deve lançar TypeError se f não for chamável.
    """
    with pytest.raises(TypeError) as excinfo:
        solve(123, 0, 1, 0, 10)
    assert str(excinfo.value) == "f must be callable"


@pytest.mark.parametrize("t0, tf, y0", [
    ("a", 1, 0),
    (0, "b", 0),
    (0, 1, "c"),
])
def test_t0_tf_y0_not_numeric_raises_type_error(t0, tf, y0):
    """
    Deve lançar TypeError se t0, tf ou y0 não forem numéricos.
    """
    f = lambda t, y: y
    with pytest.raises(TypeError) as excinfo:
        solve(f, t0, tf, y0, 10)
    assert str(excinfo.value) == "t0, tf and y0 must be numeric"


@pytest.mark.parametrize("n", [0, -1, 2.5, "x"])
def test_n_invalid_raises_value_error(n):
    """
    Deve lançar ValueError se n não for int positivo.
    """
    f = lambda t, y: y
    with pytest.raises(ValueError) as excinfo:
        solve(f, 0, 1, 0, n)
    assert str(excinfo.value) == "n must be a positive integer"